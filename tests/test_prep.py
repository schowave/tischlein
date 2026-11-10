from tischlein.prep import prep_hints


def test_frozen_marinade_dough_and_dried_legumes():
    assert prep_hints("500 g TK Lachs\n1 Zitrone") == ["Tiefkühlware am Vorabend in den Kühlschrank legen"]
    assert "Am Vorabend marinieren" in prep_hints("Marinade\n4 EL Joghurt\n600 g Hähnchen")
    assert prep_hints("500g Mehl\n1 Würfel Hefe") == ["Hefeteig braucht Zeit zum Gehen, rechtzeitig ansetzen"]
    assert prep_hints("250 g getrocknete Kichererbsen") == ["Getrocknete Hülsenfrüchte über Nacht einweichen"]


def test_canned_legumes_and_plain_recipes_need_nothing():
    assert prep_hints("1 Dose Kichererbsen (425 g EW)\n2 Tomaten") == []
    assert prep_hints("200 g Linsen, rote\n1 Zwiebel") == []
