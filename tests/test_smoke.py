def test_master_places_new_windows_in_the_stack(session):
    s = session("master")
    for title in ("w1", "w2", "w3"):
        s.open(title)
    assert s.shape() == "H[w1 V[w2 w3]]"
    assert s.alive
