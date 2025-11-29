import { useQuery } from '@tanstack/react-query';
import { api, BlockRow } from '../lib/api';

export const useBlocks = () =>
  useQuery<{ items: BlockRow[]; total: number}>({
    queryKey: ['blocks'],
    queryFn: api.getRecentBlocks,
    refetchInterval: 10000,
  });

export default useBlocks;
