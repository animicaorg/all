import { useEffect, useState } from 'react';
import type { Project } from '@animica/aicf-shared';
import { EmptyState, Panel, StatTile } from '../components/Ui';
import { aicfApi } from '../lib/api';
import { useSession } from '../lib/session';

export function ProjectsPage() {
  const { session, setSelectedProjectId } = useSession();
  const [projects, setProjects] = useState<Project[]>([]);
  const [name, setName] = useState('');
  const [slug, setSlug] = useState('');
  const [description, setDescription] = useState('');
  const [message, setMessage] = useState('');

  async function loadProjects() {
    if (!session) return;
    const payload = await aicfApi.listProjects(session);
    setProjects(payload.projects);
  }

  useEffect(() => {
    loadProjects().catch((error) => setMessage((error as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.token]);

  async function createProject() {
    if (!session) {
      setMessage('Sign in first');
      return;
    }

    try {
      const payload = await aicfApi.createProject(session, {
        name,
        slug,
        description
      });
      setProjects((prev) => [payload.project, ...prev]);
      setName('');
      setSlug('');
      setDescription('');
      setMessage(`Created project ${payload.project.name}`);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  return (
    <div className="stack">
      <Panel title="Projects" subtitle="Create and manage ANM-funded compute projects">
        <div className="grid two">
          <label>
            Project name
            <input value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <label>
            Slug
            <input value={slug} onChange={(event) => setSlug(event.target.value)} placeholder="my-team" />
          </label>
        </div>
        <label>
          Description
          <textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} />
        </label>
        <button onClick={createProject}>Create project</button>
        {message ? <p className="muted">{message}</p> : null}
      </Panel>

      {projects.length ? (
        <div className="cards-grid">
          {projects.map((project) => (
            <article key={project.id} className="tile">
              <h3>{project.name}</h3>
              <p>{project.description || 'No description'}</p>
              <div className="stats-inline">
                <StatTile label="Available ANM nanos" value={project.balance.availableAnm} />
                <StatTile label="Reserved" value={project.balance.reservedAnm} />
              </div>
              <button onClick={() => setSelectedProjectId(project.id)}>Select project</button>
            </article>
          ))}
        </div>
      ) : (
        <EmptyState title="No projects yet" detail="Create a project to issue API keys and run workloads." />
      )}
    </div>
  );
}
