"""In-memory stand-in for an SAP ECC / S/4HANA system reached over RFC.

`FakeConnection.call(name, **params)` has the same shape as `pyrfc.Connection.call`, so the
client code in field_service.py runs unchanged against a real system:

    from pyrfc import Connection
    conn = Connection(ashost=..., sysnr=..., client=..., user=..., passwd=...)

What it simulates (the behaviour the catalogue warns about):
  * BAPIRET2-style RETURN messages (TYPE S/I/W/E/A) instead of exceptions
  * Logical unit of work: BAPI_ALM_NOTIF_CREATE + _SAVE only buffer changes;
    nothing is persisted until BAPI_TRANSACTION_COMMIT on the SAME connection
  * BAPI_TRANSACTION_ROLLBACK discards the buffer
  * Temporary notification numbers (%00000000001) replaced by a real number on SAVE
  * Internal number format: equipment with leading zeros (CONVERSION_EXIT_ALPHA_INPUT)

Field names follow the standard BAPI interfaces but are simplified; verify them in
transaction BAPI / SE37 for a customer's release.
"""
from __future__ import annotations

import copy
import itertools


def ret(type_: str, message: str, id_: str = "ZDEMO", number: str = "000") -> dict:
    return {"TYPE": type_, "ID": id_, "NUMBER": number, "MESSAGE": message}


class FakeSAP:
    """The 'database'. Shared by all connections, like a real system."""

    def __init__(self):
        self.equipment = {
            "000000000010000001": {
                "DATA_GENERAL_EXP": {"DESCRIPT": "Chiller unit, Building A", "MANFACTURE": "Carrier",
                                     "MANMODEL": "30XA-252", "MANSERNO": "CX-77812", "START_FROM": "20190312"},
                "DATA_SPECIFIC_EXP": {"EQUICATGRY": "M", "MAINTPLANT": "1010", "READ_FLOC": "LDN-A-ROOF-01"},
            },
            "000000000010000002": {
                "DATA_GENERAL_EXP": {"DESCRIPT": "Lift motor, Building B", "MANFACTURE": "Otis",
                                     "MANMODEL": "Gen2-MRL", "MANSERNO": "OT-55120", "START_FROM": "20210601"},
                "DATA_SPECIFIC_EXP": {"EQUICATGRY": "M", "MAINTPLANT": "1010", "READ_FLOC": "LDN-B-CORE-02"},
            },
        }
        # (plant, material) -> unrestricted stock
        self.stock = {("1010", "FILTER-30XA"): 12.0, ("1010", "BELT-GEN2"): 0.0, ("2010", "BELT-GEN2"): 4.0}
        self.notifications: dict[str, dict] = {}
        self._notif_seq = itertools.count(10000001)

    def next_notif_no(self) -> str:
        return f"{next(self._notif_seq):012d}"


class FakeConnection:
    """One RFC session. Holds its own uncommitted buffer, as a stateful RFC connection does."""

    def __init__(self, sap: FakeSAP):
        self.sap = sap
        self._buffer: dict[str, dict] = {}       # temp no -> notification being built
        self._saved: dict[str, dict] = {}        # final no -> saved but not committed
        self._temp_seq = itertools.count(1)
        self.calls: list[str] = []

    def call(self, name: str, **params) -> dict:
        self.calls.append(name)
        fn = getattr(self, "_" + name, None)
        if fn is None:
            raise RuntimeError(f"FU_NOT_FOUND: function module {name} not available")
        return fn(**params)

    # ---- read BAPIs -------------------------------------------------------
    def _BAPI_EQUI_GETDETAIL(self, EQUIPMENT: str) -> dict:
        rec = self.sap.equipment.get(EQUIPMENT)
        if rec is None:
            return {"DATA_GENERAL_EXP": {}, "DATA_SPECIFIC_EXP": {},
                    "RETURN": ret("E", f"Equipment {EQUIPMENT.lstrip('0')} does not exist", "IE", "002")}
        return {**copy.deepcopy(rec), "RETURN": ret("S", "")}

    def _BAPI_MATERIAL_AVAILABILITY(self, PLANT: str, MATERIAL: str, UNIT: str,
                                    CHECK_RULE: str = "A", WMDVSX: list | None = None) -> dict:
        key = (PLANT, MATERIAL)
        if key not in self.sap.stock:
            return {"AV_QTY_PLT": 0.0, "WMDVEX": [],
                    "RETURN": ret("E", f"Material {MATERIAL} not maintained in plant {PLANT}", "M3", "305")}
        av = self.sap.stock[key]
        rows = []
        for req in WMDVSX or []:
            rows.append({"COM_DATE": req["REQ_DATE"], "COM_QTY": min(av, float(req["REQ_QTY"]))})
        return {"AV_QTY_PLT": av, "WMDVEX": rows, "RETURN": ret("S", "")}

    # ---- notification LUW -------------------------------------------------
    def _BAPI_ALM_NOTIF_CREATE(self, NOTIF_TYPE: str, NOTIFHEADER: dict, **_) -> dict:
        msgs = []
        if NOTIF_TYPE not in {"S1", "S2", "S3", "M1", "M2"}:
            msgs.append(ret("E", f"Notification type {NOTIF_TYPE} is not defined", "IM", "045"))
        equi = NOTIFHEADER.get("EQUIPMENT")
        if equi and equi not in self.sap.equipment:
            msgs.append(ret("E", f"Equipment {equi.lstrip('0')} does not exist", "IE", "002"))
        if not NOTIFHEADER.get("SHORT_TEXT"):
            msgs.append(ret("E", "Enter a short text", "IM", "100"))
        if msgs:
            return {"NOTIFHEADER_EXPORT": {}, "RETURN": msgs}
        temp = f"%{next(self._temp_seq):011d}"
        self._buffer[temp] = {"NOTIF_TYPE": NOTIF_TYPE, **NOTIFHEADER, "STATUS": "OSNO"}
        return {"NOTIFHEADER_EXPORT": {"NOTIF_NO": temp, **NOTIFHEADER}, "RETURN": []}

    def _BAPI_ALM_NOTIF_SAVE(self, NUMBER: str) -> dict:
        if NUMBER not in self._buffer:
            return {"NOTIFHEADER": {}, "RETURN": [ret("E", f"Notification {NUMBER} not in buffer", "IM", "050")]}
        final = self.sap.next_notif_no()
        self._saved[final] = {**self._buffer.pop(NUMBER), "NOTIF_NO": final}
        return {"NOTIFHEADER": {"NOTIF_NO": final}, "RETURN": []}

    def _BAPI_TRANSACTION_COMMIT(self, WAIT: str = "") -> dict:
        self.sap.notifications.update(self._saved)
        self._saved.clear()
        self._buffer.clear()
        return {"RETURN": ret("S", "")}

    def _BAPI_TRANSACTION_ROLLBACK(self) -> dict:
        self._saved.clear()
        self._buffer.clear()
        return {"RETURN": ret("S", "")}

    # ---- helper read for dedupe (stands in for BAPI_ALM_NOTIF_LIST / a CDS view) ----
    def _Z_OPEN_NOTIFS_FOR_EQUIPMENT(self, EQUIPMENT: str) -> dict:
        rows = [n for n in self.sap.notifications.values()
                if n.get("EQUIPMENT") == EQUIPMENT and n.get("STATUS") == "OSNO"]
        return {"NOTIFICATIONS": copy.deepcopy(rows)}
