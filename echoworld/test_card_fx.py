"""网页表现层契约：不向规则添加动作或写字段。"""
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from echoworld.api import create_app, ActionRequest


class CardEffects(unittest.TestCase):
    def test_fx_assets_controls_and_reduced_motion(self):
        with TestClient(create_app()) as client:
            html=client.get('/').text
            script=client.get('/assets/card-fx.js')
            self.assertEqual(script.status_code,200)
            self.assertIn('/assets/card-fx.js',html)
            self.assertIn('cardFxEnabled',html)
            self.assertIn('prefers-reduced-motion: reduce',script.text)
            self.assertIn('seen.has(result.request_id)',script.text)
            self.assertIn('hand.dataset.fxHandKey!==key',script.text)
            self.assertIn("if(!result.ok",script.text)
            self.assertIn("pointer-events:none",client.get('/assets/adventure-v2.css').text)

    def test_visual_helper_has_no_network_or_state_writes(self):
        script=(Path(__file__).parent/'static/assets/card-fx.js').read_text(encoding='utf-8')
        for prohibited in ('fetch(', 'XMLHttpRequest', 'localStorage', 'apply_action', 'expected_version', 'has_permit='):
            self.assertNotIn(prohibited,script)
        self.assertNotIn('card_fx',str(ActionRequest.model_fields['action'].annotation))


if __name__=='__main__':unittest.main()
