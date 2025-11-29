import { useQuery } from '@tanstack/react-query';
import { api, PoolSummary } from '../lib/api';

export const usePoolSummary = () =>
  useQuery<PoolSummary>({
    queryKey: ['pool-summary'],
    queryFn: api.getPoolSummary,
    refetchInterval: 5000,
  });

export default usePoolSummary;
