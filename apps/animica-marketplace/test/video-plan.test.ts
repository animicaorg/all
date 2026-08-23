import test from 'node:test';
import assert from 'node:assert/strict';
import { planShots, cameraFor, CAMERA_MOVES } from '../lib/videoPlan';

test('single idea → wide / main / detail coverage with distinct seeds', () => {
  const shots = planShots('a cat walking through a neon city at night', 8, { seed: 1 });
  assert.deepEqual(shots.map((s) => s.role), ['wide', 'main', 'detail']);
  assert.ok(shots[0].prompt.startsWith('wide establishing shot of a neon city at night'));
  assert.equal(shots[1].prompt, 'a cat walking through a neon city at night');
  assert.ok(shots[2].prompt.startsWith('close-up of a cat walking'));
  assert.ok(Math.abs(shots.reduce((a, s) => a + s.seconds, 0) - 8) < 0.2);
  assert.equal(new Set(shots.map((s) => s.seed)).size, 3);
  assert.ok(shots.every((s) => (CAMERA_MOVES as readonly string[]).includes(s.camera)));
});

test('short brief is one shot; beats and explicit scenes are honored', () => {
  assert.equal(planShots('a red cube', 3, { seed: 1 }).length, 1);
  const beats = planShots('A rocket sits on the pad. Then it launches into the sky. Then it orbits the earth', 12, { seed: 1 });
  assert.equal(beats.length, 3);
  assert.ok(beats[2].camera.startsWith('orbit'));
  const sc = planShots('ignored', 6, { seed: 1, scenes: ['sunrise over hills', 'a village market'] });
  assert.deepEqual(sc.map((s) => s.prompt), ['sunrise over hills', 'a village market']);
});

test('long durations repeat coverage inside shot bounds', () => {
  const shots = planShots('a lighthouse in a storm', 30, { seed: 1 });
  assert.ok(shots.length >= 6 && shots.length <= 8);
  assert.ok(shots.every((s) => s.seconds >= 1.5 && s.seconds <= 5));
  assert.ok(Math.abs(shots.reduce((a, s) => a + s.seconds, 0) - 30) < 1);
});

test('camera heuristics', () => {
  assert.equal(cameraFor('main', 0, 'drone aerial view of a coast', 4), 'tilt_down');
  assert.equal(cameraFor('wide', 0, 'a beach', 4), 'pan_right');
  assert.equal(cameraFor('wide', 1, 'a beach', 4), 'pan_left');
  assert.equal(cameraFor('detail', 2, 'x', 4), 'push_in');
});
