import type { AccountUser, ApiKeyRecord, Project, ProviderProfile } from '@animica/aicf-shared';

export type RequestContext = {
  user?: AccountUser;
  apiKey?: ApiKeyRecord;
  project?: Project;
  provider?: ProviderProfile;
};
