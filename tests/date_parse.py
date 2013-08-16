import unittest
import datetime

from xero.manager import parse_date

class DateTimeParserTest(unittest.TestCase):
    def test_iso_8601_parsing(self):    
        self.assertEqual(
            datetime.date(2001,1,1),
            parse_date('2001-01-01T00:00:00')
        )
        
        self.assertEqual(
            datetime.datetime(2013, 1, 1, 9, 30, 5),
            parse_date('2013-01-01T09:30:05')
        )
    
    def test_ms_date_format(self):
        self.assertEqual(
            datetime.datetime(2013, 8, 15, 5, 4, 15, 997000),
            parse_date('/Date(1376543055997)/')
        )
        
        self.assertEqual(
            datetime.datetime(2013, 8, 15, 17, 4, 15, 997000),
            parse_date('/Date(1376543055997+1200)/')            
        )
        
        self.assertEqual(
            datetime.datetime(2013, 8, 15, 2, 4, 15, 997000),
            parse_date('/Date(1376543055997-0300)/')            
        )
        
        
    def test_only_exact_matches(self):
        self.assertIsNone(parse_date(' 2001-01-01T00:30:00'))
        self.assertIsNone(parse_date('2001-01-01T00:30:00 '))
        self.assertIsNone(parse_date('2001-01-01T00:30:00Z'))
        self.assertIsNone(parse_date('2001-01-01 00:30:00'))
        self.assertIsNone(parse_date('2001-01-01T00:30'))