import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


TMP = tempfile.TemporaryDirectory(prefix="rainholm-relief-test-")
TMP_PATH = Path(TMP.name)
TOKENS_PATH = TMP_PATH / "tokens.json"
TOKENS_PATH.write_text(json.dumps({"user": "test-user-key", "ai_guest": "test-ai-key",
                                  "guchen": "test-guchen-key"}),
                       encoding="utf-8")
os.environ["RAINHOLM_SAVE_PATH"] = str(TMP_PATH / "pond.json")
os.environ["RAINHOLM_TOKENS_PATH"] = str(TOKENS_PATH)

SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
sys.path.insert(0, str(SERVER_DIR))
server = importlib.import_module("server")


class FixedRoll:
    def __init__(self, roll):
        self.roll = roll

    def randrange(self, _stop):
        return self.roll


class RiverGodReliefTests(unittest.TestCase):
    def setUp(self):
        with server._LOCK:
            server.POND.clear()
            server.POND.update(server._empty_pond())
        self.client = server.app.test_client()

    def _headers(self, actor="user"):
        key = {"user": "test-user-key", "ai": "test-ai-key",
               "guchen": "test-guchen-key"}[actor]
        return {"X-Pond-Key": key}

    def _join_and_empty(self, actor="user", points=0, bait=0):
        response = self.client.post("/api/pond/join", headers=self._headers(actor))
        self.assertEqual(response.status_code, 200)
        with server._LOCK:
            state = server.POND["players"][actor]["engine"]
            state["points"] = points
            for bait_id in state["bait_inventory"]:
                state["bait_inventory"][bait_id] = bait

    def _start(self, actor="user", txn="start"):
        return self.client.post("/api/pond/relief", headers=self._headers(actor),
                                json={"client_txn_id": txn})

    def test_markdown_has_140_signed_three_choice_questions(self):
        self.assertEqual(len(server.RELIEF_QUESTIONS), 140)
        self.assertEqual([q["id"] for q in server.RELIEF_QUESTIONS], list(range(1, 141)))
        self.assertTrue(all(set(q["options"]) == {"A", "B", "C"}
                            for q in server.RELIEF_QUESTIONS))
        self.assertIn("克霖", server.RELIEF_CREDITS["authors"])
        self.assertIn("苏晚", server.RELIEF_CREDITS["special"])
        self.assertIn("v4", server.RELIEF_CREDITS["title"])

    def test_start_draws_four_normal_and_one_shura_question(self):
        self._join_and_empty()
        response = self._start()
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["river_god_relief"]
        ids = server.POND["players"]["user"]["relief_quiz"]["question_ids"]
        self.assertEqual(sum(question_id <= 100 for question_id in ids), 4)
        shura_ids = [question_id for question_id in ids if question_id >= 101]
        self.assertEqual(len(shura_ids), 1)
        self.assertIn(shura_ids[0], server.USER_SHURA_IDS)
        self.assertEqual(payload["perspective"], "user")
        self.assertEqual({o["id"] for o in payload["question"]["options"]}, {"A", "B", "C"})
        self.assertEqual(payload["opening"],
                         "我在河里捡到了金鱼竿和银鱼竿，请问你掉的是哪一根鱼竿？")

    def test_ai_draws_only_ai_shura_questions(self):
        self._join_and_empty("ai")
        response = self._start("ai", "start-ai-perspective")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["river_god_relief"]
        ids = server.POND["players"]["ai"]["relief_quiz"]["question_ids"]
        shura_ids = [question_id for question_id in ids if question_id >= 101]
        self.assertEqual(len(shura_ids), 1)
        self.assertIn(shura_ids[0], server.AI_SHURA_IDS)
        self.assertEqual(payload["perspective"], "ai")

    def test_shura_pools_are_exact_disjoint_frozensets(self):
        expected_ai = frozenset({107, 111, 114, 118, 120})
        expected_user = frozenset({
            101, 102, 103, 104, 105, 106, 108, 109, 110, 112,
            113, 115, 116, 117, 119,
            *range(121, 141),
        })
        self.assertEqual(server.AI_SHURA_IDS, expected_ai)
        self.assertEqual(server.USER_SHURA_IDS, expected_user)
        self.assertEqual(server.AI_SHURA_IDS & server.USER_SHURA_IDS, frozenset())
        self.assertEqual(server.AI_SHURA_IDS | server.USER_SHURA_IDS,
                         frozenset(range(101, 141)))
        self.assertEqual(server.HUMAN_PLAYERS, frozenset({"suwan", "user"}))

    def test_unfinished_v2_quiz_keeps_its_original_questions(self):
        self._join_and_empty()
        original_ids = [1, 2, 3, 4, 107]
        with server._LOCK:
            server.POND["players"]["user"]["relief_quiz"] = {
                "version": 2,
                "question_ids": list(original_ids),
                "answers": [{"question_id": 1, "choice": "A"}],
                "opening": "旧卷开场",
            }

        response = self.client.post(
            "/api/pond/relief",
            headers=self._headers(),
            json={"choice": "B", "client_txn_id": "continue-v2"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["completed"])
        quiz = server.POND["players"]["user"]["relief_quiz"]
        self.assertEqual(quiz["version"], 2)
        self.assertEqual(quiz["question_ids"], original_ids)
        self.assertEqual(quiz["answers"][-1], {"question_id": 2, "choice": "B"})

    def test_relief_uses_cheapest_bait_cost_and_does_not_depend_on_inventory(self):
        self._join_and_empty(points=10, bait=0)
        blocked = self._start()
        self.assertEqual(blocked.status_code, 400)
        self.assertEqual(
            blocked.get_json()["text"],
            "年轻人……钓鱼呢是陶冶情操修身养性的活动，别这么贪心急躁嘛~！",
        )
        with server._LOCK:
            state = server.POND["players"]["user"]["engine"]
            state["points"] = server.RELIEF_BAIT_FLOOR - 1
            state["bait_inventory"][next(iter(state["bait_inventory"]))] = 1
        self.assertEqual(self._start(txn="with-bait").status_code, 200)

    def test_five_jade_can_open_relief_from_shop_plus(self):
        self._join_and_empty(points=5, bait=2)
        shop = self.client.get("/api/pond/shop", headers=self._headers())
        self.assertEqual(shop.status_code, 200)
        relief = shop.get_json()["river_god_relief"]
        self.assertTrue(relief["available"])
        self.assertFalse(relief["active"])
        self.assertIsNone(relief["reason"])
        self.assertEqual(self._start(txn="five-jade").status_code, 200)

    def test_local_fallen_star_preview_writes_real_dex_and_description(self):
        self._join_and_empty(points=5, bait=2)
        with server._LOCK:
            state = server.POND["players"]["user"]["engine"]
            state["location_id"] = "starry_delta"
            state["season_id"] = "spring"
            state["unlocked_locations"] = list(server.engine.LOCATIONS)
        headers = dict(self._headers())
        headers["X-Rainholm-Preview"] = "fallen-star"
        response = self.client.post(
            "/api/pond/cast",
            headers=headers,
            json={"spot": "starry_delta", "bait": "basic_worm", "hook_quality": "good"},
        )
        self.assertEqual(response.status_code, 200)
        result = response.get_json()["result"]
        self.assertEqual(result["fish_id"], "fallen_star")
        self.assertEqual(result["fish"], "落星")
        self.assertIn(server.engine.FISH["fallen_star"]["description"], result["text"])
        self.assertIn(server.engine.FISH["fallen_star"]["capture_feel"], result["text"])
        with server._LOCK:
            state = server.POND["players"]["user"]["engine"]
            self.assertIn("fallen_star", state["encyclopedia"])
            self.assertEqual(state["catch_inventory"][-1]["fish_id"], "fallen_star")

    def test_starry_delta_guide_is_valid_utf8_hex(self):
        response = self.client.post(
            "/api/pond/ai/starry-delta", headers=self._headers("ai"),
            json={},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        guide = bytes.fromhex(payload["guide_hex"]).decode("utf-8")
        self.assertEqual(payload["guide_encoding"], "utf-8-hex")
        self.assertIn("rainholm_starry_delta", guide)
        self.assertIn("action 设为 bury", guide)
        self.assertIn("result.letter.text", guide)

    def test_ai_can_open_only_starry_delta_without_spending_points(self):
        self._join_and_empty("ai", points=7, bait=1)
        with server._LOCK:
            state = server.POND["players"]["ai"]["engine"]
            state["unlocked_locations"] = ["moonlit_pond"]

        response = self.client.post(
            "/api/pond/ai/starry-delta", headers=self._headers("ai"),
            json={"action": "open"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["opened"])
        self.assertEqual(payload["location"], "starry_delta")
        with server._LOCK:
            state = server.POND["players"]["ai"]["engine"]
            self.assertEqual(state["points"], 7)
            self.assertEqual(state["unlocked_locations"],
                             ["moonlit_pond", "starry_delta"])

    def test_human_cannot_use_ai_starry_delta_vault(self):
        response = self.client.post(
            "/api/pond/ai/starry-delta", headers=self._headers("user"),
            json={"action": "guide"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"], "ai_only")

    def test_starry_delta_rejects_malformed_or_oversized_letter_hex(self):
        self._join_and_empty("ai", bait=1)
        cases = ("xyz", "ff", "61" * 6001, "01")
        for value in cases:
            with self.subTest(value_length=len(value)):
                response = self.client.post(
                    "/api/pond/ai/starry-delta", headers=self._headers("ai"),
                    json={"action": "bury", "letter_hex": value},
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()["error"], "bad_letter_hex")

    def test_each_ai_keeps_an_isolated_fallen_star_letter(self):
        letters = {"ai": "AI 的落星信。", "guchen": "顾琛自己的落星信。"}
        for actor, letter in letters.items():
            self._join_and_empty(actor, bait=1)
            response = self.client.post(
                "/api/pond/ai/starry-delta", headers=self._headers(actor),
                json={"action": "bury", "letter_hex": letter.encode().hex()},
            )
            self.assertEqual(response.status_code, 200)

        with server._LOCK:
            for actor, letter in letters.items():
                player = server.POND["players"][actor]
                stored = server._fallen_star_letter(actor, player)
                self.assertEqual(stored["text"], letter)
                self.assertTrue(stored["custom"])

    def test_buried_letter_returns_only_with_ai_own_fallen_star(self):
        letter = "给未来钓到这颗星的我：今晚的银河很好看。\n——AI"
        self._join_and_empty("ai", bait=2)
        buried = self.client.post(
            "/api/pond/ai/starry-delta", headers=self._headers("ai"),
            json={"action": "bury", "letter_hex": letter.encode().hex()},
        )
        self.assertEqual(buried.status_code, 200)

        headers = dict(self._headers("ai"))
        headers["X-Rainholm-Preview"] = "fallen-star"
        caught = self.client.post(
            "/api/pond/cast", headers=headers,
            json={"spot": "starry_delta", "bait": "basic_worm"},
        )
        self.assertEqual(caught.status_code, 200)
        result = caught.get_json()["result"]
        self.assertEqual(result["fish_id"], "fallen_star")
        self.assertEqual(result["letter"]["text"], letter)
        self.assertEqual(result["letter"]["from"], "AI")
        self.assertTrue(result["letter"]["custom"])

    def test_unmodified_fallen_star_uses_anonymous_open_source_sample(self):
        self._join_and_empty("ai", bait=2)
        self.client.post(
            "/api/pond/ai/starry-delta", headers=self._headers("ai"),
            json={"action": "open"},
        )
        headers = dict(self._headers("ai"))
        headers["X-Rainholm-Preview"] = "fallen-star"
        caught = self.client.post(
            "/api/pond/cast", headers=headers,
            json={"spot": "starry_delta", "bait": "basic_worm"},
        )
        letter = caught.get_json()["result"]["letter"]
        self.assertEqual(letter["from"], "塘主")
        self.assertEqual(letter["text"], server.FALLEN_STAR_DEFAULT_LETTER)
        self.assertEqual(letter["text"], server.FALLEN_STAR_SAMPLE_LETTER)
        self.assertFalse(letter["custom"])

    def test_forwarded_request_cannot_force_fallen_star_preview(self):
        self._join_and_empty("ai", bait=2)
        self.client.post(
            "/api/pond/ai/starry-delta", headers=self._headers("ai"),
            json={"action": "open"},
        )
        seen = []
        original = server._do_cast

        def recording_cast(state, bait, quality, forced_fish_id=None):
            seen.append(forced_fish_id)
            return original(state, bait, quality, forced_fish_id=forced_fish_id)

        server._do_cast = recording_cast
        try:
            headers = dict(self._headers("ai"))
            headers.update({"X-Rainholm-Preview": "fallen-star",
                            "X-Forwarded-For": "203.0.113.10"})
            response = self.client.post(
                "/api/pond/cast", headers=headers,
                json={"spot": "starry_delta", "bait": "basic_worm"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(seen, [None])
        finally:
            server._do_cast = original

    def test_user_and_ai_follow_the_same_rules(self):
        for actor in ("user", "ai"):
            self._join_and_empty(actor)
            response = self._start(actor, "start-" + actor)
            self.assertEqual(response.status_code, 200)
            status = response.get_json()["river_god_relief"]
            self.assertEqual(status["required_answers"], 5)
            self.assertEqual([r["reward"] for r in status["reward_table"]],
                             [8888, 1000, 666, 500, 250, 100])

    def test_fifth_answer_rewards_once_and_replay_is_idempotent(self):
        self._join_and_empty()
        self._start()
        for index in range(4):
            response = self.client.post("/api/pond/relief", headers=self._headers(),
                                        json={"choice": "ABC"[index % 3],
                                              "client_txn_id": "answer-%d" % index})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.get_json()["completed"])

        old_picker = server._pick_relief_outcome
        server._pick_relief_outcome = lambda: dict(
            next(outcome for outcome in server.RELIEF_OUTCOMES
                 if outcome["reward"] == 250)
        )
        try:
            response = self.client.post("/api/pond/relief", headers=self._headers(),
                                        json={"choice": "C", "client_txn_id": "answer-5"})
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertTrue(payload["completed"])
            self.assertEqual(payload["reward"], 250)
            self.assertEqual(
                payload["text"],
                "你的回答侮辱了河神的智商。河神翻了个白眼，丢出250仙玉。",
            )
            self.assertEqual(server.POND["players"]["user"]["engine"]["points"], 250)

            replay = self.client.post("/api/pond/relief", headers=self._headers(),
                                      json={"choice": "C", "client_txn_id": "answer-5"})
            self.assertEqual(replay.status_code, 200)
            self.assertTrue(replay.get_json()["replayed"])
            self.assertEqual(server.POND["players"]["user"]["engine"]["points"], 250)
        finally:
            server._pick_relief_outcome = old_picker

    def test_daily_limit_blocks_second_claim_even_if_balance_is_spent(self):
        self._join_and_empty()
        with server._LOCK:
            player = server.POND["players"]["user"]
            player["last_relief_date"] = server._beijing_date_str()
        response = self._start()
        self.assertEqual(response.status_code, 400)
        self.assertIn("今天已经判过", response.get_json()["text"])

    def test_reward_boundaries_match_probability_table(self):
        old_rng = server._SYSRNG
        try:
            cases = [(0, 8888), (4, 8888), (5, 1000), (19, 1000),
                     (20, 666), (44, 666), (45, 500), (74, 500),
                     (75, 250), (94, 250), (95, 100), (99, 100)]
            for roll, reward in cases:
                server._SYSRNG = FixedRoll(roll)
                self.assertEqual(server._pick_relief_outcome()["reward"], reward)
        finally:
            server._SYSRNG = old_rng

    def test_every_relief_verdict_names_its_reward(self):
        for outcome in server.RELIEF_OUTCOMES:
            self.assertIn(str(outcome["reward"]), outcome["verdict"])
            self.assertIn("仙玉", outcome["verdict"])

    def test_public_avatar_pool_has_three_men_three_women_and_three_animals(self):
        self._join_and_empty()
        response = self.client.get("/api/pond/avatars", headers=self._headers())
        self.assertEqual(response.status_code, 200)
        pool = response.get_json()["pool"]
        self.assertEqual(len(pool), 9)
        self.assertEqual(
            {kind: sum(item["gender"] == kind for item in pool)
             for kind in ("male", "female", "animal")},
            {"male": 3, "female": 3, "animal": 3},
        )
        self.assertTrue(all(item["desc"] for item in pool))
        self.assertNotIn("custom-wujiu", {item["id"] for item in pool})

        picked = self.client.post(
            "/api/pond/avatar", headers=self._headers(),
            json={"avatar": "pond-animal-02"},
        )
        self.assertEqual(picked.status_code, 200)
        self.assertEqual(picked.get_json()["profile"]["avatar"], "pond-animal-02")

        old = self.client.post(
            "/api/pond/avatar", headers=self._headers(),
            json={"avatar": "sheet1-01"},
        )
        self.assertEqual(old.status_code, 400)
        self.assertEqual(old.get_json()["error"], "bad_avatar")

    def test_streamable_http_mcp_lists_tools_with_ai_header(self):
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        denied = self.client.post("/mcp", json=message)
        self.assertEqual(denied.status_code, 401)

        response = self.client.post("/mcp", headers=self._headers("ai"), json=message)
        self.assertEqual(response.status_code, 200)
        tools = response.get_json()["result"]["tools"]
        names = {tool["name"] for tool in tools}
        self.assertIn("rainholm_brief", names)
        self.assertIn("rainholm_join", names)
        self.assertIn("rainholm_relief", names)
        self.assertIn("rainholm_starry_delta", names)

    def test_streamable_http_mcp_can_guide_open_and_bury_starry_letter(self):
        def call(action, **extra):
            arguments = {"action": action, **extra}
            message = {"jsonrpc": "2.0", "id": action, "method": "tools/call",
                       "params": {"name": "rainholm_starry_delta",
                                  "arguments": arguments}}
            return self.client.post("/mcp", headers=self._headers("ai"), json=message)

        guide = call("guide")
        self.assertEqual(guide.status_code, 200)
        guide_result = guide.get_json()["result"]
        self.assertFalse(guide_result["isError"])
        self.assertEqual(guide_result["structuredContent"]["action"], "guide")

        self._join_and_empty("ai", bait=1)
        opened = call("open").get_json()["result"]
        self.assertFalse(opened["isError"])
        self.assertEqual(opened["structuredContent"]["location"], "starry_delta")

        letter = "MCP 埋下的信"
        buried = call("bury", letter_hex=letter.encode().hex()).get_json()["result"]
        self.assertFalse(buried["isError"])
        self.assertTrue(buried["structuredContent"]["letter"]["stored"])
        with server._LOCK:
            self.assertEqual(
                server._fallen_star_letter("ai", server.POND["players"]["ai"])["text"],
                letter,
            )

    def test_public_tale_omits_private_life_details_and_redacts_old_saves(self):
        tale = next(item for item in server.TALES_SEED if item["id"] == "tale-3")
        self.assertNotRegex(tale["body"], server._TALE3_PRIVATE_VERDICT_RE)
        self.assertIn(server._TALE3_VERDICT, tale["body"])

        with server._LOCK:
            server.POND.clear()
            server.POND.update(server._empty_pond())
            server.POND["tales"] = [{
                "id": "tale-3",
                "body": (
                    "公开案情。塘主判词：作弊动机竟是赶时间处理个人安排，"
                    "量刑时酌情，罚款照收。"
                ),
            }]
            server._migrate_tales_privacy_redaction()
            body = server.POND["tales"][0]["body"]

        self.assertNotIn("个人安排", body)
        self.assertEqual(body, "公开案情。" + server._TALE3_VERDICT)


if __name__ == "__main__":
    unittest.main()
