from tischlein import season


def test_in_and_out_of_season():
    lines = ["1 Stange Lauch, in Ringen", "500 g grüner Spargel", "2 Zwiebeln"]
    assert season.seasonal(lines, 11) == (["Lauch"], ["Spargel"])
    assert season.seasonal(lines, 5) == (["Spargel"], [])
    assert season.factor(lines, 11) == season.OUT_OF_SEASON  # the asparagus weighs more than the leek
    assert season.factor(["1 Stange Lauch"], 11) == season.IN_SEASON
    assert season.factor(["2 Zwiebeln", "400 g Nudeln"], 11) == 1.0


def test_words_that_only_look_like_produce():
    # Knoblauch and Schnittlauch are no Lauch, Kürbiskerne no Kürbis, Selleriesalz no Sellerie
    lines = ["2 Zehen Knoblauch", "1 Bund Schnittlauch", "2 EL Kürbiskerne", "1 Prise Selleriesalz"]
    assert season.seasonal(lines, 11) == ([], [])
    assert season.seasonal(["1 Hokkaidokürbis", "200 g Knollensellerie"], 11) == (["Kürbis", "Sellerie"], [])


def test_frozen_and_tinned_do_not_count():
    assert season.seasonal(["450 g Spinat (TK)", "300 g Erdbeeren, gefroren"], 1) == ([], [])
    assert season.seasonal(["450 g Blattspinat"], 5) == (["Spinat"], [])
    # as written in real recipes
    lines = ["2.4 kg Grünkohl (tiefgefroren)", "750 g gefr. Kirschen", "40g Rote Bete Pulver, fein"]
    assert season.seasonal(lines, 6) == ([], [])


def test_season_wraps_over_the_new_year():
    assert "Grünkohl" in season.in_season(1) and "Grünkohl" not in season.in_season(6)
    assert season.in_season(4)[0] in ("Radieschen", "Rhabarber", "Spargel", "Spinat")  # new this month first


def test_words_with_sharp_s():
    assert season.seasonal(["2 Süßkartoffeln", "500 g Weißkraut"], 11) == (["Süßkartoffeln", "Weißkohl"], [])
