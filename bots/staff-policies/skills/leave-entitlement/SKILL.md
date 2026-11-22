---
name: leave-entitlement
description: How to work out an employee's annual leave entitlement, or the notice a leave request needs, from the leave policy's table and rules. Use for questions about how many leave days someone gets or how early to apply.
---

# Working out leave entitlement

## Days per year

1. Search the leave policy for the annual leave table, then call `get_table` with an excerpt label from it so you see every band.
2. Take the completed years of service from the question. Entitlement is based on completed years on 1 January, so 4 years and 11 months counts as 4 years.
3. Find the ONE band whose range contains that number: "Less than 2 years" means 0 or 1; "2 to 4 years" includes 2 and 4; "10 years or more" includes 10.
4. Answer with the days from that row, keep the unit "working days", and cite that row's label (for example `c3.r3`) with a quote from that row only.
5. If the question does not give the years of service, list the bands briefly and say which detail is needed.

## Part of a year

Leave accrues at one twelfth of the yearly entitlement each month. If asked how much has built up after some months, use `calculate` (for example `21 / 12 * 5`) and cite the accrual rule as the calculation's rule.

## Notice for a request

1. Find the notice rule: requests of 5 or more consecutive working days need at least 10 working days' notice; shorter requests need 2 working days.
2. If the question gives dates, call `working_days_between` to count the working days. Say that the count covers Monday to Friday only and ignores public holidays.
3. Compare the count with the rule and state both numbers. Explain the general rule; the line manager approves the request itself.
