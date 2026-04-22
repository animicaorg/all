import { describe, expect, it } from 'vitest';
import { buildTestApp } from './setup.js';

describe('admin disputes and treasury controls', () => {
  it('resolves disputes and allocates grants in ANM', () => {
    const { service, config } = buildTestApp();

    const adminLogin = service.login({
      email: config.AICF_ADMIN_BOOTSTRAP_EMAIL,
      password: config.AICF_ADMIN_BOOTSTRAP_PASSWORD
    });
    const admin = service.authenticateSession(adminLogin.token);

    const devSignup = service.signup({
      email: 'grant-dev@animica.org',
      password: 'grant-dev-password',
      role: 'developer'
    });
    const dev = service.authenticateSession(devSignup.token);

    const project = service.createProject(dev, {
      name: 'grant-project',
      slug: 'grant-project'
    });

    service.createFundingIntent(dev, {
      projectId: project.id,
      amountAnm: '8000000000000'
    });

    const key = service.createApiKey(dev, {
      projectId: project.id,
      name: 'chat-key',
      scopes: ['inference:chat', 'inference:embeddings', 'jobs:write', 'jobs:read']
    });

    const authorized = service.authorizeApiKey(key.token, 'inference:chat');
    const chat = service.runChatCompletion({
      project: authorized.project,
      apiKey: authorized.key,
      request: {
        model: 'aicf-chat-1',
        messages: [{ role: 'user', content: 'trigger a dispute for test coverage' }]
      }
    });

    const disputeJob = service.openDispute(dev, {
      jobId: chat.job.id,
      reason: 'output quality mismatch against policy'
    });
    expect(disputeJob.status).toBe('disputed');

    const resolved = service.resolveDispute(admin, {
      jobId: chat.job.id,
      action: 'uphold_provider',
      note: 'verified output is valid'
    });
    expect(resolved.status).toBe('completed');

    const grant = service.allocateGrant(admin, {
      projectId: project.id,
      amountAnmNanos: '1200000000000',
      reason: 'ecosystem onboarding grant'
    });
    expect(grant.project.id).toBe(project.id);

    const flag = service.setFeatureFlag(admin, {
      key: 'aicf.training.enabled',
      enabled: true,
      note: 'validated'
    });
    expect(flag.enabled).toBe(true);
  });
});
