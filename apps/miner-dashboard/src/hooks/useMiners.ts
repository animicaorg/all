import { useQuery } from '@tanstack/react-query';
import { api, Miner } from '../lib/api';

export const useMiners = () =>
  useQuery<{ items: Miner[]; total: number}>({
    queryKey: ['miners'],
    queryFn: api.getMiners,
    refetchInterval: 5000,
  });

export default useMiners;
