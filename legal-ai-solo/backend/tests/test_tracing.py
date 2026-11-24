"""SYNTHETIC pages shaped like vks.bg spisak-dela.jsp and pregled-delo.jsp (verified 02.10.2026)."""

from legal_ai.tracing import parse_vks_case, parse_vks_case_list, vks_case_search_url

LIST = """<div id='TablicaRezultati'><div><div>Входящ номер</div><div>Вид дело</div><div>Номер на дело</div>
<div>Отделение</div><div>Номер в пр. инстанция</div><div>Предишна инстанция</div></div>
<div><div><a href='pregled-delo.jsp?id=aaa/bbb'>1 / 01.01.2022</a></div><div>тър.</div><div>10/2022</div>
<div>1-во тър.</div><div>5/2021</div><div>Апелативен съд Пловдив</div></div>
<div><div><a href='pregled-delo.jsp?id=aaa/ccc'>2 / 01.01.2015</a></div><div>гр.</div><div></div><div></div>
<div>5/2014</div><div>Апелативен съд Пловдив</div></div></div>"""

CASE = """<div id='DeloDanni' class='Tablica'><div><div>Номер</div><div>10</div></div>
<div><div>Резултат от делото</div><div>отменя и решава по същество</div></div></div>
<div id='DeloAktove' class='Tablica'><div><div>Вид</div><div>Номер / Дата</div><div>Резултат</div></div>
<div><div><a href='pregled-akt.jsp?type=ot-delo&id=X1'>Определение</a></div>
<div><a href='pregled-akt.jsp?type=ot-delo&id=X1'>123 / 26.07.2023</a></div><div>допуска</div></div></div>
<div id='DeloIzhod' class='Tablica'><div><div>Вид на акта</div><div>Решение</div></div></div>"""


def test_case_search_url_has_previous_instance_params():
    url = vks_case_search_url(899, "Апелативен съд Пловдив")
    assert "DeloVhNoInstancia=899" in url and "DeloInstancia=" in url


def test_parse_case_list_and_case_page():
    rows = parse_vks_case_list(LIST)
    assert [r["prev_case"] for r in rows] == ["5/2021", "5/2014"]
    assert rows[0]["url"] == "https://www.vks.bg/pregled-delo.jsp?id=aaa/bbb" and rows[0]["case"] == "10/2022"
    c = parse_vks_case(CASE)
    assert c["data"]["Резултат от делото"] == "отменя и решава по същество"
    assert c["acts"] == [{"type": "Определение", "number": "123", "date": "26.07.2023", "result": "допуска",
                          "url": "https://www.vks.bg/pregled-akt.jsp?type=ot-delo&id=X1"}]
    assert c["outcome"]["Вид на акта"] == "Решение"
