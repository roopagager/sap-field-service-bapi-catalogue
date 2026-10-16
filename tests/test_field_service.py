import pytest

from bapi_demo.fake_sap import FakeConnection, FakeSAP
from bapi_demo.field_service import BapiError, FieldServiceClient, alpha_in


@pytest.fixture
def sap():
    return FakeSAP()


@pytest.fixture
def client(sap):
    return FieldServiceClient(FakeConnection(sap))


# FS-02 read equipment ---------------------------------------------------------
def test_equipment_read_pads_leading_zeros(client):
    eq = client.get_equipment("10000001")
    assert eq["manufacturer"] == "Carrier" and eq["plant"] == "1010"
    assert client.conn.calls == ["BAPI_EQUI_GETDETAIL"]


def test_unknown_equipment_raises_not_guesses(client):
    with pytest.raises(BapiError, match="does not exist"):
        client.get_equipment("99999999")


@pytest.mark.parametrize("bad", ["1' or '1'='1", "../x", "", "x" * 50])
def test_injection_rejected(bad):
    with pytest.raises(ValueError):
        alpha_in(bad)


# FS-05 parts availability -------------------------------------------------------
def test_parts_available(client):
    r = client.check_parts("FILTER-30XA", "1010", 2, "20261020")
    assert r["can_fulfil"] and r["available_at_plant"] == 12.0


def test_parts_short_and_advisory(client):
    r = client.check_parts("BELT-GEN2", "1010", 1, "20261020")
    assert not r["can_fulfil"] and "not a reservation" in r["note"]


def test_material_not_in_plant(client):
    with pytest.raises(BapiError, match="not maintained"):
        client.check_parts("FILTER-30XA", "9999", 1, "20261020")


# FS-04 notification LUW ---------------------------------------------------------
def test_notification_create_save_commit(client, sap):
    r = client.create_notification("10000001", "Chiller tripping on high pressure")
    assert r.status == "CREATED" and not r.notif_no.startswith("%")
    assert r.notif_no in sap.notifications
    assert client.conn.calls[-3:] == ["BAPI_ALM_NOTIF_CREATE", "BAPI_ALM_NOTIF_SAVE", "BAPI_TRANSACTION_COMMIT"]


def test_duplicate_refused(client, sap):
    first = client.create_notification("10000001", "Chiller tripping")
    second = client.create_notification("10000001", "Chiller tripping again")
    assert second.status == "DUPLICATE" and second.notif_no == first.notif_no
    assert len(sap.notifications) == 1


def test_error_rolls_back_and_persists_nothing(client, sap):
    r = client.create_notification("99999999", "Unknown asset")
    assert r.status == "FAILED" and "does not exist" in r.messages[0]
    assert client.conn.calls[-1] == "BAPI_TRANSACTION_ROLLBACK"
    assert sap.notifications == {}


def test_nothing_persists_without_commit(sap):
    conn = FakeConnection(sap)
    temp = conn.call("BAPI_ALM_NOTIF_CREATE", NOTIF_TYPE="S1",
                     NOTIFHEADER={"EQUIPMENT": "000000000010000002", "SHORT_TEXT": "Lift noise"})
    conn.call("BAPI_ALM_NOTIF_SAVE", NUMBER=temp["NOTIFHEADER_EXPORT"]["NOTIF_NO"])
    assert sap.notifications == {}              # saved but not committed


def test_commit_on_another_session_does_not_persist(sap):
    a, b = FakeConnection(sap), FakeConnection(sap)
    temp = a.call("BAPI_ALM_NOTIF_CREATE", NOTIF_TYPE="S1",
                  NOTIFHEADER={"EQUIPMENT": "000000000010000002", "SHORT_TEXT": "Lift noise"})
    a.call("BAPI_ALM_NOTIF_SAVE", NUMBER=temp["NOTIFHEADER_EXPORT"]["NOTIF_NO"])
    b.call("BAPI_TRANSACTION_COMMIT", WAIT="X")  # wrong session
    assert sap.notifications == {}
