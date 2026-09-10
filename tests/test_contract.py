#!/usr/bin/env python3
"""The bridge and the scenario are one interface split across two languages.

Nothing at run time checks that they agree: the bridge sends a command name
over RCON, and an unknown command comes back as an error the bridge treats as
a failed call. The map then simply lacks a layer, which reads like a game that
has nothing to report rather than a command that was never registered.

Commit 17a37b6 removed five commands from the scenario while the bridge kept
calling them. This is the test that would have caught it.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


def commands_the_scenario_registers():
    return set(re.findall(r'commands\.add_command\(\s*"([a-z_]+)"', read("scenario", "control.lua")))


def commands_the_bridge_calls():
    # Every call goes through call("/name ...") or a cache built with the same
    # string, so the leading slash is what marks a command name in the bridge.
    return set(re.findall(r'"/(chartorio[a-z_]*)', read("bridge", "bridge.py")))


class Contract(unittest.TestCase):
    def test_the_scenario_registers_everything_the_bridge_calls(self):
        missing = commands_the_bridge_calls() - commands_the_scenario_registers()
        self.assertEqual(missing, set(),
                         "the bridge calls commands the scenario does not register, so "
                         "those layers are dead: %s" % ", ".join(sorted(missing)))

    def test_no_entity_search_is_left_unbounded(self):
        """An entity search costs time proportional to the area searched, not to
        what it finds. A search without an `area` walks the whole surface, which
        is the thing this project keeps having to take back out."""
        scenario = read("scenario", "control.lua")
        unbounded = []
        for match in re.finditer(r"find_entities_filtered\(\{", scenario):
            # The argument table ends at the matching brace; a search never
            # nests one, so the first close is the right one.
            tail = scenario[match.end():scenario.index("}", match.end())]
            if "area" not in tail:
                line = scenario.count("\n", 0, match.start()) + 1
                unbounded.append(line)
        self.assertEqual(unbounded, [],
                         "find_entities_filtered without an area at line(s) %s"
                         % ", ".join(str(line) for line in unbounded))

    def test_the_signal_hook_is_actually_assigned(self):
        """`local remember_signal_hook` followed by a `local function` of the
        same name makes a second local and leaves the hook nil forever, which
        is how the signal registry silently stopped updating once before."""
        scenario = read("scenario", "control.lua")
        self.assertIn("remember_signal_hook = function", scenario,
                      "the forward declaration is never assigned")

    def test_the_regexes_still_find_something(self):
        # A test that silently matches nothing would pass forever.
        self.assertIn("chartorio", commands_the_scenario_registers())
        self.assertIn("chartorio", commands_the_bridge_calls())


if __name__ == "__main__":
    unittest.main()
