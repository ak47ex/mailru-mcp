"""Разбор free-busy ответа Mail.ru (schedule-response) и слияние занятости участников."""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("MAILRU_USER", "me@example.com")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mailru_calendar as cal  # noqa: E402

UTC = timezone.utc
MSK = cal.ZoneInfo("Europe/Moscow")


def _z(s):
    return datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


def _response(addr, status="2.0;Success", fb_lines=None):
    """Один C:response; форма как в реальном ответе Mail.ru."""
    data = ""
    if fb_lines is not None:
        data = ("<C:calendar-data>BEGIN:VCALENDAR\nPRODID:-//MailRu//MailRu Calendar API -//EN\n"
                "VERSION:2.0\nMETHOD:REPLY\nBEGIN:VFREEBUSY\nUID:probe-20261106T091410Z\n"
                "DTSTART:20261106T091410Z\nDTEND:20261113T091410Z\nDTSTAMP:20261106T091411Z\n"
                "ORGANIZER:mailto:me@example.com\nATTENDEE:mailto:" + addr + "\n"
                + "\n".join(fb_lines) + "\nEND:VFREEBUSY\nEND:VCALENDAR\n</C:calendar-data>")
    return ('<C:response><C:recipient><D:href xmlns:D="DAV:">mailto:' + addr + "</D:href>"
            "</C:recipient><C:request-status>" + status + "</C:request-status>"
            + data + "</C:response>")


def _doc(*responses):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<C:schedule-response xmlns:C="urn:ietf:params:xml:ns:caldav">'
            + "".join(responses) + "</C:schedule-response>")


# Реальный ответ (обезличен): коллега с многодневным BUSY, коллега с обычными встречами
# и несуществующий адрес с FREE на всё окно.
REAL = _doc(
    _response("colleague1@example.com", fb_lines=[
        "FREEBUSY;FBTYPE=BUSY:20261105T000000Z/20261107T000000Z",
        "FREEBUSY;FBTYPE=BUSY:20261109T073000Z/20261109T083000Z",
        "FREEBUSY;FBTYPE=BUSY:20261109T090000Z/20261109T093000Z"]),
    _response("colleague2@example.com", fb_lines=[
        "FREEBUSY;FBTYPE=BUSY:20261106T090000Z/20261106T103000Z",
        "FREEBUSY;FBTYPE=BUSY:20261106T110000Z/20261106T120000Z"]),
    _response("typo@example.", fb_lines=[
        "FREEBUSY;FBTYPE=FREE:20261106T091410Z/20261113T091410Z"]),
)


class ParseScheduleResponse(unittest.TestCase):
    def test_real_response(self):
        r = cal.parse_schedule_response(REAL)
        self.assertEqual(set(r), {"colleague1@example.com", "colleague2@example.com",
                                  "typo@example."})
        c1 = r["colleague1@example.com"]
        self.assertIsNone(c1["error"])
        self.assertEqual(c1["status"], "2.0;Success")
        self.assertEqual(len(c1["busy"]), 3)
        self.assertEqual(c1["busy"][0],
                         (_z("20261105T000000Z"), _z("20261107T000000Z"), "BUSY"))
        self.assertEqual(len(r["colleague2@example.com"]["busy"]), 2)

    def test_free_is_not_busy(self):
        r = cal.parse_schedule_response(REAL)["typo@example."]
        self.assertEqual(r["busy"], [])
        self.assertIsNone(r["error"])

    def test_bytes_input(self):
        r = cal.parse_schedule_response(REAL.encode("utf-8"))
        self.assertEqual(len(r), 3)

    def test_folding(self):
        line = "FREEBUSY;FBTYPE=BUSY:20261105T09\r\n 0000Z/2026110\r\n 5T100000Z"
        r = cal.parse_schedule_response(_doc(_response("a@example.com", fb_lines=[line])))
        self.assertEqual(r["a@example.com"]["busy"],
                         [(_z("20261105T090000Z"), _z("20261105T100000Z"), "BUSY")])

    def test_multiple_periods_and_lines(self):
        r = cal.parse_schedule_response(_doc(_response("a@example.com", fb_lines=[
            "FREEBUSY;FBTYPE=BUSY:20261105T090000Z/20261105T100000Z,"
            "20261105T110000Z/20261105T120000Z,20261105T130000Z/20261105T140000Z",
            "FREEBUSY;FBTYPE=BUSY:20261106T090000Z/20261106T100000Z"])))
        self.assertEqual(len(r["a@example.com"]["busy"]), 4)

    def test_duration(self):
        r = cal.parse_schedule_response(_doc(_response("a@example.com", fb_lines=[
            "FREEBUSY:20261105T090000Z/PT1H,20261106T090000Z/PT1H30M,20261107T090000Z/P1D"])))
        ends = [e - s for s, e, _ in r["a@example.com"]["busy"]]
        self.assertEqual(ends, [timedelta(hours=1), timedelta(hours=1, minutes=30),
                                timedelta(days=1)])

    def test_fbtypes(self):
        r = cal.parse_schedule_response(_doc(_response("a@example.com", fb_lines=[
            "FREEBUSY:20261105T090000Z/20261105T100000Z",
            "FREEBUSY;FBTYPE=busy-tentative:20261105T110000Z/20261105T120000Z",
            "FREEBUSY;FBTYPE=BUSY-UNAVAILABLE:20261105T130000Z/20261105T140000Z",
            "FREEBUSY;FBTYPE=FREE:20261105T150000Z/20261105T160000Z"])))
        self.assertEqual([t for _, _, t in r["a@example.com"]["busy"]],
                         ["BUSY", "BUSY-TENTATIVE", "BUSY-UNAVAILABLE"])

    def test_error_status(self):
        for status in ("3.7;Invalid calendar user", "5.1;Service unavailable"):
            r = cal.parse_schedule_response(_doc(_response("a@example.com", status=status)))
            self.assertEqual(r["a@example.com"]["error"], status)
            self.assertEqual(r["a@example.com"]["busy"], [])

    def test_empty_calendar_data(self):
        r = cal.parse_schedule_response(_doc(_response("a@example.com")))
        self.assertEqual(r["a@example.com"]["error"], "пустой ответ")

    def test_unparsable_period_marks_error(self):
        r = cal.parse_schedule_response(_doc(_response("a@example.com", fb_lines=[
            "FREEBUSY:20261105T090000Z/PT1H,garbage/garbage"])))
        self.assertEqual(len(r["a@example.com"]["busy"]), 1)
        self.assertEqual(r["a@example.com"]["error"], "не удалось разобрать FREEBUSY")

    def test_address_normalized(self):
        r = cal.parse_schedule_response(_doc(_response("Boss@Example.COM", fb_lines=[
            "FREEBUSY:20261105T090000Z/20261105T100000Z"])))
        self.assertIn("boss@example.com", r)

    def test_not_xml(self):
        with self.assertRaises(RuntimeError):
            cal.parse_schedule_response("<html>oops")


class BuildRequest(unittest.TestCase):
    def test_request(self):
        now = _z("20261106T091411Z")
        body = cal.build_freebusy_request(
            ["a@example.com", "b@example.com"], _z("20261106T000000Z"), _z("20261107T000000Z"),
            "uid-1", now)
        self.assertTrue(body.endswith("\r\n"))
        self.assertNotIn("\n", body.replace("\r\n", ""))
        for s in ("METHOD:REQUEST", "BEGIN:VFREEBUSY", "UID:uid-1", "DTSTAMP:20261106T091411Z",
                  "DTSTART:20261106T000000Z", "DTEND:20261107T000000Z",
                  "ORGANIZER:mailto:" + cal.USER, "ATTENDEE:mailto:a@example.com",
                  "ATTENDEE:mailto:b@example.com"):
            self.assertIn(s + "\r\n", body)


class FreebusyValidation(unittest.TestCase):
    def test_too_many(self):
        with self.assertRaises(ValueError):
            cal.freebusy([f"u{i}@example.com" for i in range(cal.MAX_ATTENDEES + 1)],
                         _z("20261106T000000Z"), _z("20261107T000000Z"))

    def test_only_garbage_no_network(self):
        r = cal.freebusy(["garbage", "Garbage", "a@b, c@d"],
                         _z("20261106T000000Z"), _z("20261107T000000Z"))
        self.assertEqual(list(r), ["garbage", "a@b, c@d"])
        for v in r.values():
            self.assertEqual(v["error"], "некорректный адрес")


class MergeAttendeeBusy(unittest.TestCase):
    WS = datetime(2026, 11, 6, tzinfo=MSK)
    WE = datetime(2026, 11, 7, tzinfo=MSK)

    def _res(self):
        return cal.parse_schedule_response(_doc(
            _response("busy@example.com", fb_lines=[
                "FREEBUSY;FBTYPE=BUSY:20261106T090000Z/20261106T100000Z",
                "FREEBUSY;FBTYPE=BUSY-TENTATIVE:20261106T110000Z/20261106T120000Z"]),
            _response("free@example.com", fb_lines=[
                "FREEBUSY;FBTYPE=FREE:20261106T000000Z/20261107T000000Z"]),
            _response("bad@example.com", status="3.7;Invalid calendar user"),
        ))

    def test_tentative_busy(self):
        busy, info = cal.merge_attendee_busy(self._res(), self.WS, self.WE, True)
        self.assertEqual(len(busy), 2)
        self.assertEqual(busy[0][0], datetime(2026, 11, 6, 12, 0, tzinfo=MSK))
        self.assertEqual(busy[0][0].utcoffset(), timedelta(hours=3))

    def test_tentative_not_busy(self):
        busy, info = cal.merge_attendee_busy(self._res(), self.WS, self.WE, False)
        self.assertEqual(len(busy), 1)
        row = info[0]
        self.assertEqual((row["busy_intervals"], row["tentative_intervals"]), (1, 1))
        self.assertTrue(row["busy_found"])

    def test_warning_and_error(self):
        _, info = cal.merge_attendee_busy(self._res(), self.WS, self.WE, True)
        by = {r["address"]: r for r in info}
        self.assertEqual([r["address"] for r in info],
                         ["busy@example.com", "free@example.com", "bad@example.com"])
        self.assertNotIn("warning", by["busy@example.com"])
        self.assertFalse(by["free@example.com"]["busy_found"])
        self.assertIn("возможно адрес неверный", by["free@example.com"]["warning"])
        self.assertEqual(by["bad@example.com"]["error"], "3.7;Invalid calendar user")
        self.assertNotIn("warning", by["bad@example.com"])

    def test_clip_and_outside(self):
        res = cal.parse_schedule_response(_doc(_response("a@example.com", fb_lines=[
            "FREEBUSY:20261105T000000Z/20261107T000000Z",
            "FREEBUSY:20261201T090000Z/20261201T100000Z"])))
        busy, info = cal.merge_attendee_busy(res, self.WS, self.WE, True)
        self.assertEqual(busy, [(self.WS, self.WE)])
        self.assertEqual(info[0]["busy_intervals"], 1)


if __name__ == "__main__":
    unittest.main()
