import { describe, expect, it } from 'vitest';
import { buildTestApp } from './setup.js';

describe('auth + wallet + api-key flow', () => {
  it('creates user, links wallet, funds project, and executes chat completion', () => {
    const { service } = buildTestApp();

    const signup = service.signup({
      email: 'dev@animica.org',
      password: 'dev-password-123',
      role: 'developer'
    });

    const user = service.authenticateSession(signup.token);

    const linked = service.linkWallet(user, {
      address: 'anm1devwalletxyz',
      chainId: 1337,
      signature: '0xwalletsig'
    });
    expect(linked.wallet?.address).toBe('anm1devwalletxyz');

    const project = service.createProject(user, {
      name: 'AICF Demo',
      slug: 'aicf-demo',
      description: 'ANM native compute project'
    });

    const funded = service.createFundingIntent(user, {
      projectId: project.id,
      amountAnm: '5000000000000',
      txHash: '0xabc'
    });

    expect(BigInt(funded.project.balance.availableAnm)).toBeGreaterThan(0n);

    const key = service.createApiKey(user, {
      projectId: project.id,
      name: 'default-key',
      scopes: ['inference:chat', 'inference:embeddings', 'jobs:read', 'jobs:write']
    });

    const authorized = service.authorizeApiKey(key.token, 'inference:chat');
    const completion = service.runChatCompletion({
      project: authorized.project,
      apiKey: authorized.key,
      request: {
        model: 'aicf-chat-1',
        messages: [{ role: 'user', content: 'Summarize ANM-native billing.' }],
        max_tokens: 64
      }
    });

    expect(completion.response.model).toBe('aicf-chat-1');
    expect((completion.response.choices as Array<{ message: { content: string } }>)[0].message.content).toContain(
      'ANM-native'
    );
  });
});
