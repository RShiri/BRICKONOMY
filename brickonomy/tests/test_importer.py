"""BrickEconomy CSV import — both export shapes, offline."""
import pytest

from brickonomy import db as dbq
from brickonomy.importer import import_csv, parse_condition

NEW_FORMAT = """Number,Theme,Subtheme,Year,SetName,Retail,Paid,Value,Condition,Date,Notes
76049-1,Marvel,Avengers,2016,Avenjet Space Mission,59.99,90.00,182.99,UsedAsNew,2024-03-01,
76029-1,Marvel,Age of Ultron,2015,Iron Man vs. Ultron,12.99,9.00,50.00,UsedComplete,,
10283-1,Space,NASA,2021,Shuttle Discovery,199.99,199.99,300.00,New,,
31199-1,Marvel,,2020,Iron Man Art,119.99,77.00,120.00,UsedComplete,,
31199-1,Marvel,,2020,Iron Man Art,119.99,65.00,120.00,UsedComplete,,
"""

OLD_FORMAT = """Number,Theme,Subtheme,Year,Name,Owned,Wanted,Retail
75192-1,Star Wars,UCS,2017,Millennium Falcon,1,0,799.99
75252-1,Star Wars,UCS,2019,Star Destroyer,0,1,699.99
"""


@pytest.fixture()
def conn(tmp_path):
    c = dbq.connect(db_path=str(tmp_path / "imp.db"))
    yield c
    c.close()


def write(tmp_path, text, name="export.csv"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


class TestConditionMapping:
    @pytest.mark.parametrize("raw,expected", [
        ("New", "new"), ("Sealed", "new"),
        ("UsedAsNew", "used"), ("UsedComplete", "used"),
        ("UsedIncomplete", "used"), ("", "new"),
    ])
    def test_anything_opened_is_used(self, raw, expected):
        assert parse_condition(raw) == expected


class TestNewFormat:
    def test_rows_without_an_owned_column_still_land_in_the_portfolio(
            self, conn, tmp_path):
        """The bug: the importer read a missing Owned column as 0, so a
        new-format export imported metadata and no holdings at all."""
        summary = import_csv(conn, write(tmp_path, NEW_FORMAT))
        assert summary["rows"] == 5
        assert summary["portfolio"] == 4      # 5 rows, one set owned twice
        assert len(dbq.get_portfolio(conn)) == 4

    def test_purchase_price_is_what_was_paid_not_retail(self, conn, tmp_path):
        import_csv(conn, write(tmp_path, NEW_FORMAT))
        row = conn.execute(
            "SELECT * FROM portfolio WHERE item_id='76049'").fetchone()
        assert row["purchase_price"] == pytest.approx(90.00)   # not 59.99
        assert row["purchase_date"] == "2024-03-01"

    def test_condition_is_carried_through(self, conn, tmp_path):
        import_csv(conn, write(tmp_path, NEW_FORMAT))
        cond = {r["item_id"]: r["condition"] for r in dbq.get_portfolio(conn)}
        assert cond["76049"] == "used"        # UsedAsNew is still used
        assert cond["10283"] == "new"

    def test_the_set_name_column_is_read(self, conn, tmp_path):
        import_csv(conn, write(tmp_path, NEW_FORMAT))
        assert dbq.get_item(conn, "76029")["name"] == "Iron Man vs. Ultron"

    def test_two_copies_aggregate_instead_of_overwriting(self, conn, tmp_path):
        """Owning a set twice is two rows; the second must not replace the
        first, and the average price must still total what was spent."""
        summary = import_csv(conn, write(tmp_path, NEW_FORMAT))
        assert summary["duplicates"] == 1
        row = conn.execute(
            "SELECT * FROM portfolio WHERE item_id='31199'").fetchone()
        assert row["owned"] == 2
        assert row["purchase_price"] == pytest.approx(71.00)      # (77+65)/2
        assert row["owned"] * row["purchase_price"] == pytest.approx(142.00)

    def test_total_paid_survives_the_import(self, conn, tmp_path):
        import_csv(conn, write(tmp_path, NEW_FORMAT))
        total = conn.execute(
            "SELECT SUM(purchase_price * owned) t FROM portfolio").fetchone()["t"]
        assert total == pytest.approx(90 + 9 + 199.99 + 142.00)


class TestOldFormat:
    def test_owned_and_wanted_columns_are_still_honoured(self, conn, tmp_path):
        summary = import_csv(conn, write(tmp_path, OLD_FORMAT))
        assert summary["portfolio"] == 1
        assert summary["wishlist"] == 1
        rows = {r["item_id"]: r for r in dbq.get_portfolio(conn)}
        assert rows["75192"]["owned"] == 1
        assert conn.execute(
            "SELECT wanted FROM portfolio WHERE item_id='75252'"
        ).fetchone()["wanted"] == 1

    def test_falls_back_to_retail_when_there_is_no_paid_column(
            self, conn, tmp_path):
        import_csv(conn, write(tmp_path, OLD_FORMAT))
        row = conn.execute(
            "SELECT * FROM portfolio WHERE item_id='75192'").fetchone()
        assert row["purchase_price"] == pytest.approx(799.99)


class TestSoldOff:
    """An export is a snapshot of what is owned now; what it omits was sold."""

    def _seed_extra_holdings(self, conn):
        # An owned set the export will not mention (sold), a wishlist-only
        # row, and a minifig the sets export could never describe.
        # 21318 is deliberately absent from NEW_FORMAT: the sold one.
        dbq.upsert_item(conn, "21318", name="Tree House", item_type="S")
        dbq.upsert_portfolio(conn, "21318", owned=1, purchase_price=199.99)
        dbq.upsert_item(conn, "76008", name="Wanted set", item_type="S")
        dbq.upsert_portfolio(conn, "76008", owned=0, wanted=1)
        dbq.upsert_item(conn, "sh0229", name="A minifig", item_type="M")
        dbq.upsert_portfolio(conn, "sh0229", owned=1)
        conn.commit()

    def test_missing_sets_are_reported_but_not_removed_by_default(
            self, conn, tmp_path):
        self._seed_extra_holdings(conn)
        summary = import_csv(conn, write(tmp_path, NEW_FORMAT))
        assert [s["item_id"] for s in summary["sold"]] == ["21318"]
        assert summary["removed"] == 0
        # Still there: reporting must not delete anything on its own.
        assert conn.execute(
            "SELECT COUNT(*) c FROM portfolio WHERE item_id='21318'"
        ).fetchone()["c"] == 1

    def test_prune_removes_them(self, conn, tmp_path):
        self._seed_extra_holdings(conn)
        summary = import_csv(conn, write(tmp_path, NEW_FORMAT), prune=True)
        assert summary["removed"] == 1
        assert conn.execute(
            "SELECT COUNT(*) c FROM portfolio WHERE item_id='21318'"
        ).fetchone()["c"] == 0

    def test_wishlist_and_other_item_types_are_never_touched(
            self, conn, tmp_path):
        """A sets export lists no minifigures, so a missing fig is not a sold
        fig — pruning on one must not wipe out the other."""
        self._seed_extra_holdings(conn)
        import_csv(conn, write(tmp_path, NEW_FORMAT), prune=True)
        kept = {r["item_id"] for r in conn.execute(
            "SELECT item_id FROM portfolio")}
        assert "sh0229" in kept, "minifig holdings survive a sets import"
        assert "76008" in kept, "wishlist rows are not holdings"

    def test_reimporting_the_same_file_is_idempotent(self, conn, tmp_path):
        path = write(tmp_path, NEW_FORMAT)
        import_csv(conn, path)
        first = {r["item_id"]: r["owned"] for r in dbq.get_portfolio(conn)}
        import_csv(conn, path)
        second = {r["item_id"]: r["owned"] for r in dbq.get_portfolio(conn)}
        assert first == second, "quantities must not creep on re-import"
        assert second["31199"] == 2


MIXED_CURRENCY = """Number,Theme,Subtheme,Year,SetName,Retail,Paid,Value,Condition,Date,Notes
31199-1,Marvel,,2020,Iron Man Art,119.99,$100.00,120.00,UsedComplete,,
31199-1,Marvel,,2020,Iron Man Art,119.99,₪350.00,120.00,UsedComplete,,
"""

MESSY_QTY = """Number,Theme,Subtheme,Year,Name,Owned,Wanted,Retail
75192-1,Star Wars,UCS,2017,Millennium Falcon,not a number,,799.99
75252-1,Star Wars,UCS,2019,Star Destroyer,2,0,699.99
"""


class TestAwkwardInput:
    def test_two_copies_in_different_currencies_are_converted_not_added(
            self, conn, tmp_path):
        """100 USD + 350 ILS is not 450 of anything. At 3.5 ILS/USD both
        copies are 350 ILS, so the USD average must come out at 100."""
        dbq.upsert_rate(conn, "USD", "ILS", 3.5)
        conn.commit()
        import_csv(conn, write(tmp_path, MIXED_CURRENCY))
        row = conn.execute(
            "SELECT * FROM portfolio WHERE item_id='31199'").fetchone()
        assert row["owned"] == 2
        assert row["purchase_currency"] == "USD"       # first copy's currency
        assert row["purchase_price"] == pytest.approx(100.0)
        assert row["owned"] * row["purchase_price"] == pytest.approx(200.0)

    def test_a_non_numeric_quantity_does_not_abort_the_import(
            self, conn, tmp_path):
        """One bad cell used to raise ValueError straight out of the web
        import handler."""
        summary = import_csv(conn, write(tmp_path, MESSY_QTY))
        assert summary["rows"] == 2
        rows = {r["item_id"]: r["owned"] for r in dbq.get_portfolio(conn)}
        assert rows == {"75252": 2}, "the good row still imports"


class TestMissingFile:
    def test_a_missing_csv_is_reported_not_raised(self, conn, tmp_path):
        summary = import_csv(conn, str(tmp_path / "nope.csv"))
        assert summary["rows"] == 0
        assert dbq.get_portfolio(conn) == []
        # Same keys as a real run, so callers can read them unguarded.
        assert summary["sold"] == [] and summary["removed"] == 0
        assert summary["duplicates"] == 0


class TestPrintedParts:
    """A printed part is neither a set nor a minifigure, but its id starts
    with a digit, so everything that was not a minifig fell through to "set".
    Eight Ant-Man and Captain America statuettes sat in the catalog as sets,
    each scanned a dozen times as if it were one."""

    def test_a_printed_part_is_not_a_set(self):
        from brickonomy.importer import item_type_for
        assert item_type_for("90398pb007") == "P"
        assert item_type_for("90398pb004c01") == "P", "mould-variant suffix"

    def test_real_sets_with_letters_in_the_id_stay_sets(self):
        from brickonomy.importer import item_type_for
        # These are genuine sets: BrickLink splits a boxed pair into 4679a
        # and 4679b, and the Christmas ornaments into 4169306a/b/c.
        for set_id in ("4679a", "4169306a", "5370b", "75192", "10283"):
            assert item_type_for(set_id) == "S", set_id

    def test_minifigs_are_untouched(self):
        from brickonomy.importer import item_type_for
        for fig in ("sh0254", "col334", "cty1837"):
            assert item_type_for(fig) == "M", fig

    def test_printed_parts_do_not_appear_on_either_catalog_tab(self, tmp_path):
        from brickonomy import db as dbq
        path = str(tmp_path / "pp.db")
        c = dbq.connect(db_path=path)
        dbq.upsert_item(c, "90398pb007", name="Ant-Man Statuette",
                        item_type="P")
        dbq.upsert_item(c, "75192", name="Falcon", item_type="S")
        dbq.upsert_item(c, "sh0254", name="Iron Man", item_type="M")
        c.commit()
        sets = {r["item_id"] for r in dbq.list_items(c, item_type="S")}
        figs = {r["item_id"] for r in dbq.list_items(c, item_type="M")}
        c.close()
        assert "90398pb007" not in sets and "90398pb007" not in figs
        assert sets == {"75192"} and figs == {"sh0254"}
