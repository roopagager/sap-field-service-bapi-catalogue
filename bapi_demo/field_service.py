"""Field-service client over SAP BAPIs: FS-02, FS-05 (read) and FS-04 (write).

Takes any object with `.call(name, **params)` - a pyrfc.Connection in production,
FakeConnection in tests and demos. The rules from the catalogue live here, in code:

  * pad keys with leading zeros (CONVERSION_EXIT_ALPHA_INPUT)
  * read every RETURN message; E/A means failure, never guess
  * writes: CREATE -> SAVE -> COMMIT in one session; ROLLBACK on any E/A
  * dedupe: refuse a new notification if one is already open for the equipment
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_KEY = re.compile(r"^[A-Za-z0-9\-]{1,40}$")


class BapiError(Exception):
    def __init__(self, bapi: str, messages: list[dict]):
        self.bapi, self.messages = bapi, messages
        super().__init__(f"{bapi}: " + "; ".join(m["MESSAGE"] for m in messages))


def alpha_in(value: str, length: int = 18) -> str:
    """CONVERSION_EXIT_ALPHA_INPUT: numeric keys get leading zeros, others pass through."""
    value = safe_key(value)
    return value.zfill(length) if value.isdigit() else value


def safe_key(value: str) -> str:
    if not isinstance(value, str) or not _KEY.match(value):
        raise ValueError(f"invalid SAP key: {value!r}")
    return value


def _errors(ret) -> list[dict]:
    msgs = ret if isinstance(ret, list) else [ret]
    return [m for m in msgs if m and m.get("TYPE") in ("E", "A")]


@dataclass
class NotificationResult:
    status: str                      # CREATED | DUPLICATE | FAILED
    notif_no: str | None = None
    messages: list[str] = field(default_factory=list)


class FieldServiceClient:
    def __init__(self, conn):
        self.conn = conn

    # FS-02 -------------------------------------------------------------------
    def get_equipment(self, equipment: str) -> dict:
        out = self.conn.call("BAPI_EQUI_GETDETAIL", EQUIPMENT=alpha_in(equipment))
        if errs := _errors(out["RETURN"]):
            raise BapiError("BAPI_EQUI_GETDETAIL", errs)
        g, s = out["DATA_GENERAL_EXP"], out["DATA_SPECIFIC_EXP"]
        return {"equipment": equipment.lstrip("0"), "description": g["DESCRIPT"],
                "manufacturer": g["MANFACTURE"], "model": g["MANMODEL"], "serial_no": g["MANSERNO"],
                "plant": s["MAINTPLANT"], "functional_location": s["READ_FLOC"]}

    # FS-05 -------------------------------------------------------------------
    def check_parts(self, material: str, plant: str, quantity: float, req_date: str,
                    unit: str = "PC") -> dict:
        out = self.conn.call("BAPI_MATERIAL_AVAILABILITY", PLANT=safe_key(plant), MATERIAL=safe_key(material),
                             UNIT=unit, CHECK_RULE="A",
                             WMDVSX=[{"REQ_DATE": safe_key(req_date), "REQ_QTY": str(quantity)}])
        if errs := _errors(out["RETURN"]):
            raise BapiError("BAPI_MATERIAL_AVAILABILITY", errs)
        committed = sum(r["COM_QTY"] for r in out["WMDVEX"])
        return {"material": material, "plant": plant, "available_at_plant": out["AV_QTY_PLT"],
                "requested": quantity, "can_fulfil": committed >= quantity,
                "note": "Advisory only - not a reservation. The work order component reserves stock."}

    # FS-04 -------------------------------------------------------------------
    def create_notification(self, equipment: str, short_text: str, priority: str = "2",
                            notif_type: str = "S1", reported_by: str = "FIELD") -> NotificationResult:
        equi = alpha_in(equipment)
        open_ = self.conn.call("Z_OPEN_NOTIFS_FOR_EQUIPMENT", EQUIPMENT=equi)["NOTIFICATIONS"]
        if open_:
            return NotificationResult("DUPLICATE", open_[0]["NOTIF_NO"],
                                      [f"Open notification {open_[0]['NOTIF_NO'].lstrip('0')} already exists"])
        header = {"EQUIPMENT": equi, "SHORT_TEXT": short_text[:40], "PRIORITY": priority,
                  "REPORTEDBY": reported_by[:12]}
        created = self.conn.call("BAPI_ALM_NOTIF_CREATE", NOTIF_TYPE=notif_type, NOTIFHEADER=header)
        if errs := _errors(created["RETURN"]):
            self.conn.call("BAPI_TRANSACTION_ROLLBACK")
            return NotificationResult("FAILED", messages=[m["MESSAGE"] for m in errs])
        temp = created["NOTIFHEADER_EXPORT"]["NOTIF_NO"]
        saved = self.conn.call("BAPI_ALM_NOTIF_SAVE", NUMBER=temp)
        if errs := _errors(saved["RETURN"]):
            self.conn.call("BAPI_TRANSACTION_ROLLBACK")
            return NotificationResult("FAILED", messages=[m["MESSAGE"] for m in errs])
        self.conn.call("BAPI_TRANSACTION_COMMIT", WAIT="X")
        return NotificationResult("CREATED", saved["NOTIFHEADER"]["NOTIF_NO"])
