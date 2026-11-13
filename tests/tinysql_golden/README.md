The test scripts of myTinySQL, copied from the C++ and Rust versions, and the tables they run on.

One line differs on purpose: in `create.expected`, the error for an unknown type also offers `DATE`,
which only the Python version has. Everything else is unchanged, byte for byte.

**All data here is sample data, for testing purposes only.** The companies, addresses and other values are
fictional or AI generated. Many names in the `EMPLOY` table are those of historical Italian composers, used only
as sample values; the other values in those rows (such as city and period) are sample data and may not be
accurate. Any other resemblance to real persons, living or dead, or actual places is purely coincidental.
