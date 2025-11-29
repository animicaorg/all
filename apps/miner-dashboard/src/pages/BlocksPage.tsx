import BlocksTable from '../components/Tables/BlocksTable';
import useBlocks from '../hooks/useBlocks';

const BlocksPage = () => {
  const { data } = useBlocks();

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-2xl font-semibold">Blocks</h2>
        <p className="text-white/60 text-sm">Recent pool and chain blocks.</p>
      </div>
      <BlocksTable blocks={data?.items ?? []} />
    </div>
  );
};

export default BlocksPage;
