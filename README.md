# SAP Field Service — BAPI Integration Catalogue

Five SAP BAPIs that automate one field-service job end to end in **SAP ECC / S/4HANA Plant Maintenance (PM) and Customer Service (CS)**, written as an integration catalogue for an AI agent or integration layer.

A reference design by Roopa Gager, not tied to any client.

## How the five fit one job

```
Fault reported ─► FS-04 create service notification
Triage         ─► FS-02 read equipment master
Parts?         ─► FS-05 check material availability
Dispatch       ─► FS-01 create work order (referencing the notification)
Job done       ─► FS-03 confirm work order operation
```

Read BAPIs (FS-02, FS-05) are safe for an agent to call freely. Write BAPIs (FS-01, FS-03, FS-04) need a commit, an idempotency check and, for anything with cost or finality, a human approval step.

## 1. Catalogue

| ID | Step | BAPI · business object | Type | Key inputs | Key outputs | Commit / LUW | S/4HANA API alternative | Agent tool & guardrail |
|---|---|---|---|---|---|---|---|---|
| **FS-01** | Create work order | **BAPI_ALM_ORDER_MAINTAIN** · BUS2007 maintenance order / BUS2088 service order | Write · sync RFC | `IT_METHODS` (HEADER-CREATE, OPERATION-CREATE, SAVE) · `IT_HEADER`: ORDER_TYPE, PLANPLANT, MN_WK_CTR, EQUIPMENT, NOTIF_NO, SHORT_TEXT, START_DATE · `IT_OPERATION`: ACTIVITY, CONTROL_KEY, WORK_CNTR, DESCRIPTION, WORK_ACTIVITY · `IT_COMPONENT` (parts) | `RETURN` · `ET_NUMBERS` (temporary `%00000000001` → real order number) | `BAPI_TRANSACTION_COMMIT` (WAIT = 'X') in the same RFC session; rollback on any E/A message | API_MAINTENANCEORDER | `create_work_order` · approval before create · idempotency key = notification no. |
| **FS-02** | Read equipment master | **BAPI_EQUI_GETDETAIL** · EQUI equipment | Read · sync RFC | EQUIPMENT (18 chars, leading zeros) | `DATA_GENERAL_EXP` (description, manufacturer, model, serial no., start-up date) · `DATA_SPECIFIC_EXP` (plant, functional location, category) · `RETURN` | None — read only | API_EQUIPMENT | `get_equipment` · read-only, no approval |
| **FS-03** | Confirm work order operation | **BAPI_ALM_CONF_CREATE** · PM/CS order confirmation | Write · sync RFC | `TIMETICKETS`: ORDERID, OPERATION, ACT_WORK, UN_WORK, FIN_CONF, CLEAR_RES, POSTG_DATE, EXEC_START/FIN date + time, CONF_TEXT, PERS_NO | `RETURN` · `DETAIL_RETURN` (CONF_NO, CONF_CNT per ticket) | `BAPI_TRANSACTION_COMMIT`; one commit per job, not per row | Maintenance order confirmation API (check api.sap.com for your release) | `confirm_operation` · technician sign-off · final confirmation only with explicit approval · check existing confirmations first |
| **FS-04** | Create service notification | **BAPI_ALM_NOTIF_CREATE** + **BAPI_ALM_NOTIF_SAVE** · BUS2080 service / BUS2038 maintenance notification | Write · sync RFC | NOTIF_TYPE (e.g. S1–S3 service, M1–M2 maintenance) · `NOTIFHEADER`: SHORT_TEXT, EQUIPMENT, FUNCT_LOC, PRIORITY, REPORTEDBY, BREAKDOWN · `NOTITEM` / `NOTIFCAUS` (damage, cause codes) · `LONGTEXTS` | `NOTIFHEADER_EXPORT` (temporary number) · final NOTIF_NO after SAVE · `RETURN` | CREATE → SAVE → `BAPI_TRANSACTION_COMMIT`, all in one session | API_MAINTNOTIFICATION | `create_notification` · no approval for field-reported faults · dedupe on equipment + open notification |
| **FS-05** | Check material availability | **BAPI_MATERIAL_AVAILABILITY** · availability check (ATP) | Read · sync RFC | PLANT · MATERIAL · UNIT · CHECK_RULE · STGE_LOC (optional) · `WMDVSX`: REQ_DATE, REQ_QTY | `AV_QTY_PLT` (available at plant) · `WMDVEX`: COM_DATE, COM_QTY · `RETURN` | None — read only | Product availability information API (S/4HANA advanced ATP; check api.sap.com) | `check_parts` · read-only · advisory, **not a reservation** |

## 2. Integration notes per BAPI

| ID | Watch out for | Error handling | Authorisation (customer SAP roles) |
|---|---|---|---|
| **FS-01** | Temporary numbers link header, operations and components in `IT_METHODS`; the SAVE method is mandatory. Customer user exits and BAdIs run, so test in their system. | Read every `RETURN` row; any E or A → `BAPI_TRANSACTION_ROLLBACK` and surface the message. Order locked → retry with backoff. | S_RFC for the BAPI function group, plus PM/CS order objects such as I_IWERK (planning plant) |
| **FS-02** | Pad equipment numbers with leading zeros (`CONVERSION_EXIT_ALPHA_INPUT`). Texts are language-dependent. | E = not found or no authority → return "not found", never guess. | S_RFC, plus equipment display authority |
| **FS-03** | Blind retries create duplicate confirmations. A final confirmation closes the operation and can trigger costs and settlement. | Read existing confirmations (`BAPI_ALM_CONF_GETLIST`) before retrying. Use `DETAIL_RETURN` to find the failed ticket. | S_RFC, plus confirmation authority for order type and plant |
| **FS-04** | Without `BAPI_ALM_NOTIF_SAVE` the notification is lost. Damage and cause codes must exist in the customer's catalog profile. | Rollback on E/A. Invalid code → ask for a valid one rather than dropping it. | S_RFC, plus notification authority (e.g. I_QMEL) and plant |
| **FS-05** | Result depends on the customer's checking rule and scope of check, and is a snapshot. | Material not in plant → tell the user and suggest another plant. | S_RFC, plus material display authority |

## 3. Rules that apply to all five

- **Connectivity:** call RFC through SAP Cloud Connector + a BTP destination, or PyRFC / SAP JCo / SAP .NET Connector from the customer network. Many customers wrap BAPIs as REST in SAP Integration Suite; on S/4HANA, prefer the released OData APIs above.
- **Commit in the same session:** `BAPI_TRANSACTION_COMMIT` only works on the same stateful RFC connection that ran the BAPI. Stateless HTTP wrappers must run BAPI + commit in one request.
- **Exactly once:** for writes from queues or retries, use tRFC/bgRFC or your own idempotency key, so a timeout never creates two orders or two confirmations.
- **Identity:** use principal propagation so SAP checks the real technician's roles; a shared technical user hides who did what.
- **Audit:** log every call with user, BAPI, key inputs, returned numbers and RETURN messages.
- **Verify per customer:** check parameter and field names in transaction `BAPI` / `SE37` for the customer's release, and confirm order and notification types with their PM/CS team.

## Design principle

Expose two read tools freely (equipment, parts availability) and three write tools behind controls: notifications can be auto-created with dedupe, while work orders and final confirmations need human approval, a commit in the same RFC session and an idempotency key so retries never create duplicates.

See also: [sap-p2p-copilot](https://github.com/roopagager/sap-p2p-copilot) — the same pattern applied to procure-to-pay over S/4HANA OData APIs.
