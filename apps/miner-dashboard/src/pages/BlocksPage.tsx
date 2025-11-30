import BlocksTable from '../components/Tables/BlocksTable';
import useBlocks from '../hooks/useBlocks';
import DataState from '../components/Feedback/DataState';

const BlocksPage = () => {
  const { data, isLoading, isError, error } = useBlocks();

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-2xl font-semibold">Blocks</h2>
        <p className="text-white/60 text-sm">Recent pool and chain blocks.</p>
      </div>
      <DataState isLoading={isLoading} isError={isError} errorMessage={error instanceof Error ? error.message : undefined}>
        <BlocksTable blocks={data?.items ?? []} />
      </DataState>
    </div>
  );
};

export default BlocksPage;
