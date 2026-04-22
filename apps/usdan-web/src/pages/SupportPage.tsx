import { useEffect, useState } from 'react';
import { usdanApi } from '../lib/api';
import { useSession } from '../lib/session';

export function SupportPage() {
  const { session } = useSession();
  const [subject, setSubject] = useState('');
  const [message, setMessage] = useState('');
  const [tickets, setTickets] = useState<any[]>([]);
  const [status, setStatus] = useState('');

  useEffect(() => {
    if (!session) return;
    usdanApi.listSupportTickets(session).then((res) => setTickets(res.tickets)).catch(() => undefined);
  }, [session]);

  async function createTicket() {
    if (!session) return;
    try {
      const result = await usdanApi.createSupportTicket(session, {
        subject,
        message,
        priority: 'MEDIUM'
      });
      setTickets((prev) => [result.ticket, ...prev]);
      setSubject('');
      setMessage('');
      setStatus('Support ticket created');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Failed to create ticket');
    }
  }

  return (
    <section className="page">
      <h2>Support</h2>
      {!session ? <p className="warning">Create a session to submit support tickets.</p> : null}

      <div className="card">
        <label>
          Subject
          <input value={subject} onChange={(e) => setSubject(e.target.value)} />
        </label>
        <label>
          Message
          <textarea value={message} onChange={(e) => setMessage(e.target.value)} />
        </label>
        <button disabled={!session || !subject || !message} onClick={createTicket}>Submit Ticket</button>
      </div>

      <h3>Recent Tickets</h3>
      <div className="table">
        {tickets.map((ticket) => (
          <div key={ticket.id} className="row">
            <span>{ticket.subject}</span>
            <span>{ticket.status}</span>
            <span>{ticket.priority}</span>
          </div>
        ))}
      </div>

      {status ? <p className="status">{status}</p> : null}
    </section>
  );
}
