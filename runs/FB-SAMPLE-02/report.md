# Feedback Report FB-SAMPLE-02

**Review status:** pending_review  |  **Confidence:** high  |  **Needs careful review:** YES

## Input
- Customer: `-` / `alice.nguyen@northwind.example`  |  Channel: chat  |  Submitted: 2026-09-29T02:40:00+00:00

> Since this morning's update, CSV export fails with 'Error 500' for every user on our workspace. Our whole operations team (250 people) relies on the daily export to plan shipments and we have nothing today. We need this fixed ASAP.

## Classification
- **Category:** bug_report
- **Urgency:** critical  |  **Sentiment:** negative  |  **Classifier confidence:** 0.98
- **Rationale:** The feedback states 'CSV export fails with Error 500 for every user' and 'our whole operations team relies on the daily export,' indicating a core functionality is blocked for a large group. The request for ASAP resolution further signals critical urgency.

## Summary
The customer, Alice Nguyen from Northwind Logistics (enterprise tier), reports that since a recent update, the CSV export feature fails with 'Error 500' for all users in their workspace. This issue is blocking their entire operations team (250 users) from performing daily shipment planning and is classified as a critical bug affecting core functionality.

## Customer context
Alice Nguyen (C001) is an enterprise-tier customer representing Northwind Logistics, with 250 seats and a high monthly spend. The reported issue is impacting all users in their workspace, and there are no open tickets currently. Past tickets do not indicate a similar issue.

## Policy / guideline references
- `SOP-BUG-01` Handling Bug Reports: Outlines steps for handling bug reports, including escalation for core functionality outages affecting enterprise customers.
- `POL-ESC-003` Escalation Rules: Requires immediate escalation to Engineering on-call for bugs blocking core features for enterprise customers or affecting multiple users.
- `POL-SLA-002` Support Response SLA: Mandates a 1-hour first response for critical issues for enterprise customers and reporting SLA breaches to the account manager.
- `POL-CREDIT-007` Service Credit Policy: Entitles enterprise customers to a 10% service credit if a core feature is unavailable for more than 4 hours.

## Suggested next actions
1. Confirm the affected feature (CSV export), platform, and steps to reproduce with the customer; request screenshots if not already provided. (owner: CS officer) [basis: `SOP-BUG-01`]
2. Check past tickets for duplicate reports and link if found; otherwise, file a new engineering ticket with critical severity, including customer tier, ticket ID, and summary of impact. (owner: CS officer) [basis: `SOP-BUG-01`]
3. Immediately escalate the issue to Engineering on-call as it blocks core functionality for an enterprise customer and affects all users. (owner: CS officer) [basis: `POL-ESC-003`]
4. Acknowledge the issue to the customer within 1 hour and provide ongoing status updates, per SLA. (owner: CS officer) [basis: `POL-SLA-002`]
5. Notify the account manager (Minh Hoang) of the SLA breach and critical impact. (owner: CS officer) [basis: `POL-SLA-002`]
6. If the outage exceeds 4 hours, initiate a 10% service credit for the customer’s next invoice. (owner: CS officer) [basis: `POL-CREDIT-007`]

## Flags
- **HIGH_RISK**: bug_report / critical urgency.
