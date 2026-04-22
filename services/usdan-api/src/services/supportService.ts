import type { UsdanStore } from '../store/types.js';

export class SupportService {
  constructor(private readonly store: UsdanStore) {}

  async createTicket(input: {
    userId: string;
    subject: string;
    message: string;
    priority: 'LOW' | 'MEDIUM' | 'HIGH' | 'URGENT';
  }) {
    return this.store.createSupportTicket({
      userId: input.userId,
      subject: input.subject,
      message: input.message,
      priority: input.priority,
      status: 'OPEN'
    });
  }

  async listTickets(userId?: string) {
    return this.store.listSupportTickets(userId);
  }
}
