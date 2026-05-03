import type { PrismaClient } from '@prisma/client';

export function toInt(value: unknown): number {
  if (typeof value === 'bigint') return Number(value);
  if (typeof value === 'number') return value;
  if (typeof value === 'string') return Number.parseInt(value, 10) || 0;
  return 0;
}

export function pagination(page: number, limit: number, total: number) {
  return {
    page,
    limit,
    total,
    totalPages: Math.ceil(total / limit),
  };
}

export async function countSql(
  prisma: PrismaClient,
  sql: string,
  ...values: unknown[]
): Promise<number> {
  try {
    const rows = await prisma.$queryRawUnsafe<Array<{ count: bigint | number | string }>>(
      sql,
      ...values
    );
    return toInt(rows[0]?.count);
  } catch {
    return 0;
  }
}

export async function rowsSql<T>(
  prisma: PrismaClient,
  sql: string,
  ...values: unknown[]
): Promise<T[]> {
  try {
    return await prisma.$queryRawUnsafe<T[]>(sql, ...values);
  } catch {
    return [];
  }
}

export async function tableExists(prisma: PrismaClient, tableName: string): Promise<boolean> {
  try {
    const rows = await prisma.$queryRaw<Array<{ exists: boolean }>>`
      SELECT EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = ${tableName}
      ) AS exists
    `;
    return rows[0]?.exists ?? false;
  } catch {
    return false;
  }
}

export async function columnExists(
  prisma: PrismaClient,
  tableName: string,
  columnName: string
): Promise<boolean> {
  try {
    const rows = await prisma.$queryRaw<Array<{ exists: boolean }>>`
      SELECT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = ${tableName}
          AND column_name = ${columnName}
      ) AS exists
    `;
    return rows[0]?.exists ?? false;
  } catch {
    return false;
  }
}
