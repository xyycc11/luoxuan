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

    def test_music_is_only_a_floating_accessible_toggle(self):
        with TestClient(create_app()) as client:
            html=client.get('/').text
            css=client.get('/assets/adventure-v2.css').text
            self.assertIn('id="secretMusicToggle"',html)
            self.assertIn('aria-pressed="false"',html)
            self.assertIn('id="secretAudio" loop preload="none"',html)
            self.assertNotIn('id="secretAudio" controls',html)
            for removed in ('id="secretMusicPlay"','id="secretMusicStop"','id="secretMusicFile"','id="secretMusicConsent"','class="secret-music"'):
                self.assertNotIn(removed,html)
            self.assertIn('.music-dock{position:fixed;',css)
            self.assertIn('/assets/adventure-v2.css?v=20261023-elements',html)
            self.assertIn('safe-area-inset-bottom',css)

    def test_all_seven_original_element_icons_are_local_webp(self):
        with TestClient(create_app()) as client:
            for key in ('pyro','hydro','electro','cryo','dendro','anemo','geo'):
                response=client.get('/assets/elements/'+key+'.webp')
                self.assertEqual(response.status_code,200,key)
                self.assertEqual(response.content[:4],b'RIFF')
                self.assertEqual(response.content[8:12],b'WEBP')
                self.assertEqual(response.headers['content-type'].split(';')[0],'image/webp')

    def test_element_card_illustrations_preserve_readable_actions(self):
        html=(Path(__file__).parent/'static/index.html').read_text(encoding='utf-8')
        self.assertIn('const ELEMENT_ART=',html)
        self.assertIn("emblem.src='/assets/elements/'+option.element+'.webp'",html)
        self.assertIn("button.append(meta,name,description)",html)
        self.assertIn("!['hold','parry'].includes(option.kind)",html)


if __name__=='__main__':unittest.main()
