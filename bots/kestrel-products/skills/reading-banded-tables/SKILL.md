---
name: reading-banded-tables
description: How to answer from a table whose rows are ranges or bands (scheme size, number of members, age, salary). Use when a question gives a number and the answer depends on which row it falls in.
---

# Reading a banded table

Use this when the answer comes from a table whose rows cover ranges, for example "250 to 499 members", "aged 6 to 21" or "1000 and above".

1. Search for the table, then call `get_table` with the label of any excerpt from it, so you see every row with its header.
2. Take the number from the question (for example 320 members). Do not round it.
3. Go through the rows in order and find the ONE row whose range contains the number. Check both ends: "250 to 499" contains 250 and 499; "less than 2" does not contain 2; "1000 and above" contains 1000.
4. Read the value from the column the question asks about (for example the group life column, not the disability column). Keep the unit printed in the header or cell, such as "per month".
5. Cite that row's label (such as `c4.r4`) with a quote copied from that row only. Do not cite the whole table, and do not quote a neighbouring row.
6. If the number sits exactly on a boundary, or the table has no row for it, say so and give the rows either side rather than guessing.
7. If the question does not give the number the table needs, say what the caller must tell you (for example the number of members) and do not pick a row.
