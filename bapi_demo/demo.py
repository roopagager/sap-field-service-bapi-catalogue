"""Walk one field-service job through the BAPIs:  python -m bapi_demo.demo"""
from .fake_sap import FakeConnection, FakeSAP
from .field_service import BapiError, FieldServiceClient

sap = FakeSAP()
fs = FieldServiceClient(FakeConnection(sap))

print("1. Fault reported on equipment 10000002")
r = fs.create_notification("10000002", "Lift stopping between floors", priority="1")
print(f"   FS-04 notification: {r.status} {r.notif_no.lstrip('0')}")

print("2. Same fault reported again by another engineer")
r2 = fs.create_notification("10000002", "Lift stuck on floor 3")
print(f"   FS-04 notification: {r2.status} - {r2.messages[0]}")

print("3. Triage: read the equipment master")
eq = fs.get_equipment("10000002")
print(f"   FS-02 {eq['description']} | {eq['manufacturer']} {eq['model']} | plant {eq['plant']}")

print("4. Parts: drive belt needed")
for plant in ("1010", "2010"):
    p = fs.check_parts("BELT-GEN2", plant, 1, "20261020")
    print(f"   FS-05 plant {plant}: available {p['available_at_plant']:g} -> can fulfil: {p['can_fulfil']}")

print("5. Bad input from the field app")
try:
    fs.get_equipment("99999999")
except BapiError as e:
    print(f"   FS-02 refused: {e}")

print(f"\nCommitted notifications in SAP: {len(sap.notifications)}")
