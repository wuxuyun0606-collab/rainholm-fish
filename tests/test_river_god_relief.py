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
TOKENS_PATH.write_text(json.dumps({"user": "test-user-key", "ai_guest": "test-ai-key"}),
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
        key = "test-user-key" if actor == "user" else "test-ai-key"
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

    def test_markdown_has_120_signed_three_choice_questions(self):
        self.assertEqual(len(server.RELIEF_QUESTIONS), 120)
        self.assertEqual([q["id"] for q in server.RELIEF_QUESTIONS], list(range(1, 121)))
        self.assertTrue(all(set(q["options"]) == {"A", "B", "C"}
                            for q in server.RELIEF_QUESTIONS))
        self.assertIn("克霖", server.RELIEF_CREDITS["authors"])
        self.assertIn("苏晚", server.RELIEF_CREDITS["special"])

    def test_start_draws_four_normal_and_one_shura_question(self):
        self._join_and_empty()
        response = self._start()
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["river_god_relief"]
        ids = server.POND["players"]["user"]["relief_quiz"]["question_ids"]
        self.assertEqual(sum(question_id <= 100 for question_id in ids), 4)
        self.assertEqual(sum(question_id >= 101 for question_id in ids), 1)
        self.assertEqual({o["id"] for o in payload["question"]["options"]}, {"A", "B", "C"})
        self.assertEqual(payload["opening"],
                         "我在河里捡到了金鱼竿和银鱼竿，请问你掉的是哪一根鱼竿？")

    def test_relief_requires_zero_points_but_does_not_depend_on_bait(self):
        self._join_and_empty(points=10, bait=0)
        blocked = self._start()
        self.assertEqual(blocked.status_code, 400)
        self.assertEqual(
            blocked.get_json()["text"],
            "年轻人……钓鱼呢是陶冶情操修身养性的活动，别这么贪心急躁嘛~！",
        )
        with server._LOCK:
            state = server.POND["players"]["user"]["engine"]
            state["points"] = 0
            state["bait_inventory"][next(iter(state["bait_inventory"]))] = 1
        self.assertEqual(self._start(txn="with-bait").status_code, 200)

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


if __name__ == "__main__":
    unittest.main()
