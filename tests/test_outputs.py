"""Two outputs of different sizes and scales, like a laptop with a monitor."""

CONFIG = ("output HEADLESS-1 resolution 1920x1200 position 0 480 scale 1.25\n"
          "output HEADLESS-2 resolution 2560x1440 position 1536 0\n"
          "workspace 10 output HEADLESS-1\nworkspace 1 output HEADLESS-2\nworkspace 3 output HEADLESS-2\n"
          "workspace 4 output HEADLESS-1\n"
          "bindsym Mod4+F1 nop layout move left\nbindsym Mod4+F2 nop layout move right\n"
          "bindsym Mod4+F3 nop layout move number 10\nbindsym Mod4+F6 nop layout move number 3\n")
LAYOUTS = {"1": "master", "10": "stacked-master", "3": "float", "4": "tabbed"}


def floats_fit(s, name):
    ws = s.workspace(name)
    output = next(o for o in s.tree()["nodes"] if any(w["id"] == ws["id"] for w in o["nodes"]))
    area = output["rect"]
    return all(area["x"] <= n["rect"]["x"] and n["rect"]["x"] + n["rect"]["width"] <= area["x"] + area["width"]
               and area["y"] <= n["rect"]["y"] and n["rect"]["y"] + n["rect"]["height"] <= area["y"] + area["height"]
               for n in ws["floating_nodes"])


def test_two_outputs(session):
    s = session("master", workspaces=LAYOUTS, config=CONFIG, outputs=2)
    s.command("workspace 1")
    for title in ("a1", "a2", "a3"):
        s.open(title)
    s.command("workspace 10")
    s.open("b1")
    s.open("b2")
    assert (s.shape("1"), s.shape("10")) == ("H[a1 V[a2 a3]]", "H[b1 S[b2]]")
    s.focus("a1")
    s.open("a4")
    s.focus("b1")
    s.open("b3")
    assert (s.shape("1"), s.shape("10")) == ("H[a1 V[a2 a3 a4]]", "H[b1 S[b2 b3]]")
    s.focus("a4")
    s.key("F3")
    assert (s.shape("1"), s.shape("10")) == ("H[a1 V[a2 a3]]", "H[b1 S[b2 b3 a4]]")
    s.focus("a4")
    s.key("F1")
    s.key("F1")
    assert s.shape("10") == "H[a4 b1 S[b2 b3]]"
    s.open("b4")
    assert s.shape("10") == "H[a4 b1 S[b2 b3 b4]]"
    s.focus("b2")
    s.key("F2")
    assert s.shape("10") == "H[a4 b1 S[b3 b4] b2]"
    s.key("F2")
    assert (s.shape("1"), s.shape("10")) == ("H[a1 V[a2 a3 b2]]", "H[a4 b1 S[b3 b4]]")
    s.focus("a2")
    s.key("F6")
    s.command("workspace 3")
    s.open("c1")
    assert s.shape("3") == "- F[a2 c1]" and floats_fit(s, "3")
    s.focus("c1")
    s.key("F1")
    assert s.shape("10") == "H[a4 b1 S[b3 b4 c1]]"
    s.command("workspace 3")
    s.command("move workspace to output left")
    assert floats_fit(s, "3")
    s.command("workspace 3")
    s.open("c2")
    assert s.shape("3") == "- F[a2 c2]" and floats_fit(s, "3")
    s.command("workspace 1")
    s.command("output HEADLESS-2 unplug")
    assert s.alive
    s.command("workspace 1")
    s.open("a5")
    assert s.shape("1") == "H[a1 V[a3 b2 a5]]"
    s.command("create_output")
    s.command("workspace 4")
    s.open("d1")
    s.command("rename workspace 4 to four")
    s.open("d2")
    s.open("d3")
    assert s.shape("four") == "T[d1 d2 d3]"
    assert s.chosen("four") == "tabbed"
    assert s.alive
