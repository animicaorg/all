import { useQuery } from '@tanstack/react-query';
import { api, MinerDetail } from '../lib/api';

export const useMinerDetail = (workerId: string) =>
  useQuery<MinerDetail>({
    queryKey: ['miner', workerId],
    queryFn: () => api.getMinerDetail(workerId),
    enabled: Boolean(workerId),
    refetchInterval: 7000,
  });

export default useMinerDetail;
