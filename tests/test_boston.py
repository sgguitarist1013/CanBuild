from datetime import date
import asyncio
from unittest.mock import AsyncMock, patch
import httpx
import pytest
import boston
from parcel_engine import requested_house_number, normalize_address, same_street


def test_spelled_number_and_wharf_suffix():
    assert normalize_address('One Lewis Wharf, Boston, MA')=='1 Lewis Wharf, Boston, MA'
    assert requested_house_number('One Lewis Wharf, Boston, MA')=='1'
    assert same_street('LEWIS WH','LEWIS WHARF')
    assert not same_street('LEWIS PL','LEWIS WHARF')


def test_boston_exact_match_does_not_infer_owner_alias():
    a={'ST_NUM':38,'ST_NAME':'ATLANTIC AV','OWNER':'ONE LEWIS WHARF LLC'}
    assert not boston.matches(a,'1','LEWIS WHARF')
    assert boston.matches({'ST_NUM':1,'ST_NAME':'LEWIS WH','UNIT_NUM':None},'1','LEWIS WHARF')
    assert not boston.matches({'ST_NUM':1,'ST_NAME':'LEWIS WH','UNIT_NUM':'2'},'1','LEWIS WHARF')


def test_owner_and_selected_record_labels():
    p,_=boston.to_result({'PID':'0303029000','OWNER':'ONE LEWIS WHARF LLC','FULL_ADDRESS':'38-34 ATLANTIC AV'},'User-selected')
    assert p['recorded_owners']==['ONE LEWIS WHARF LLC']
    assert p['match_method']=='User-selected'


def test_sale_filters_exclude_subject_wrong_use_stale_and_nominal_transfers():
    subject={'PID':'1','LUC':'344','GROSS_AREA':17000}
    records=[{'PID':str(i),'LUC':'344','GROSS_AREA':15000} for i in range(1,7)]
    records[3]['LUC']='101';records[4]['GROSS_AREA']=100
    sales=[{'parcel_id':str(i),'latest_sales_date':'2026-01-01','latest_sales_price':1000000} for i in range(1,7)]
    sales[2]['latest_sales_price']=1;sales[5]['latest_sales_date']='2001-01-01'
    out=boston.filter_sales(records,sales,subject,date(2026,11,6))
    assert [x['parcel_number'] for x in out]==['2']
    assert out[0]['price_per_gross_sqft']==67


def test_ambiguous_alias_returns_candidate_without_selecting():
    row={'attributes':{'PID':'0303029000','OWNER':'ONE LEWIS WHARF LLC','FULL_ADDRESS':'38-34 ATLANTIC AV','ST_NUM':38,'ST_NAME':'ATLANTIC AV'}}
    with patch('boston.fetch',new=AsyncMock(side_effect=[{'features':[]},{'features':[row]}])):
        record,candidates,method,truncated=asyncio.run(boston.lookup(None,-71.05,42.36,{'streetName':'LEWIS WHARF'},'1 Lewis Wharf, Boston, MA'))
    assert record is None
    assert candidates[0]['parcel_number']=='0303029000'
    assert truncated is False


def test_incomplete_exact_search_keeps_candidate_without_verifying_parcel():
    row = {"attributes": {"PID": "0303029000", "FULL_ADDRESS": "1 LEWIS WHARF",
                          "ST_NUM": 1, "ST_NAME": "LEWIS WHARF"}}
    with patch("boston.fetch", new=AsyncMock(side_effect=[
        {"features": [row], "exceededTransferLimit": True}, {"features": []},
    ])):
        result = asyncio.run(boston.lookup(None, -71.05, 42.36, {"streetName": "LEWIS WHARF"},
                                           "1 Lewis Wharf, Boston, MA"))
    assert result[0] is None
    assert result[1][0]["parcel_number"] == "0303029000"
    assert result[3] is True


def test_incomplete_explicit_id_search_does_not_verify_parcel():
    row = {"attributes": {"PID": "0303029000"}}
    with patch("boston.fetch", new=AsyncMock(return_value={"features": [row], "exceededTransferLimit": True})):
        with pytest.raises(ValueError, match="incomplete"):
            asyncio.run(boston.lookup(None, -71.05, 42.36, {}, "1 Lewis Wharf, Boston, MA", "0303029000"))


@pytest.mark.parametrize("followup,expected_pids,truncated", [
    ({"features": [{"attributes": {"PID": "0303029000", "FULL_ADDRESS": "1 LEWIS WHARF"}}]},
     ["0303029000", "neighbor"], False),
    ({"features": [], "exceededTransferLimit": True}, ["neighbor"], True),
    (httpx.ReadTimeout("Second page unavailable"), ["neighbor"], True),
])
def test_boston_spatial_search_fetches_one_more_page_when_incomplete(followup, expected_pids, truncated):
    fetch = AsyncMock(side_effect=[
        {"features": []},
        {"features": [{"attributes": {"PID": "neighbor"}}], "exceededTransferLimit": True},
        followup,
    ])
    with patch("boston.fetch", new=fetch):
        record, candidates, _, incomplete = asyncio.run(boston.lookup(
            None, -71.05, 42.36, {"streetName": "LEWIS WHARF"}, "1 Lewis Wharf, Boston, MA",
        ))
    assert record is None
    assert [row["parcel_number"] for row in candidates] == expected_pids
    assert incomplete is truncated
    first_page, second_page = [call.args[2] for call in fetch.await_args_list[1:]]
    assert first_page["orderByFields"] == "OBJECTID ASC"
    assert first_page["resultOffset"] == 0
    assert second_page == {**first_page, "resultOffset": first_page["resultRecordCount"]}
