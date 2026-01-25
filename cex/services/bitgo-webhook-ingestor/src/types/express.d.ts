/**
 * Type augmentations for Express
 */

import "express";

declare module "express" {
  export interface Request {
    /**
     * Request ID for tracking
     */
    id?: string;

    /**
     * Service authentication context (if applicable)
     */
    serviceAuth?: {
      serviceId: string;
      issuer: string;
      scopes: string[];
    };
  }
}
