/**
 * Contract Templates
 */

export interface ContractTemplate {
  id: string;
  name: string;
  description: string;
  files: {
    path: string;
    content: string;
  }[];
  manifest: any;
}

/**
 * Hello World Template
 */
export const helloWorldTemplate: ContractTemplate = {
  id: "hello-world",
  name: "Hello World",
  description: "Minimal contract with a single storage value",
  files: [
    {
      path: "src/main.py",
      content: `"""
Hello World Contract
A minimal example that stores and retrieves a greeting message.
"""

from animica.storage import Storage
from animica.abi import public

storage = Storage()

@public
def deploy(greeting: str):
    """Initialize contract with a greeting message"""
    storage.set("greeting", greeting)

@public
def get_greeting() -> str:
    """Get the stored greeting"""
    return storage.get("greeting", "")

@public
def set_greeting(greeting: str):
    """Update the greeting message"""
    storage.set("greeting", greeting)
`,
    },
  ],
  manifest: {
    manifestVersion: "1.0.0",
    encoding: "animica-manifest/1",
    package: {
      name: "hello-world",
      version: "0.1.0",
      description: "Hello World Contract",
    },
    target: {
      vm: "python",
      vmVersion: "1.0.0",
      abiVersion: "1.0.0",
    },
    entrypoint: "src/main.py",
  },
};

/**
 * Counter Template
 */
export const counterTemplate: ContractTemplate = {
  id: "counter",
  name: "Counter",
  description: "Simple counter with increment/decrement operations",
  files: [
    {
      path: "src/main.py",
      content: `"""
Counter Contract
Stores a counter value with increment and decrement operations.
"""

from animica.storage import Storage
from animica.abi import public

storage = Storage()

@public
def deploy():
    """Initialize counter to 0"""
    storage.set("count", 0)

@public
def increment():
    """Increment counter by 1"""
    count = storage.get("count", 0)
    storage.set("count", count + 1)

@public
def decrement():
    """Decrement counter by 1"""
    count = storage.get("count", 0)
    storage.set("count", count - 1)

@public
def get_count() -> int:
    """Get current counter value"""
    return storage.get("count", 0)

@public
def reset():
    """Reset counter to 0"""
    storage.set("count", 0)
`,
    },
  ],
  manifest: {
    manifestVersion: "1.0.0",
    encoding: "animica-manifest/1",
    package: {
      name: "counter",
      version: "0.1.0",
      description: "Counter Contract",
    },
    target: {
      vm: "python",
      vmVersion: "1.0.0",
      abiVersion: "1.0.0",
    },
    entrypoint: "src/main.py",
  },
};

/**
 * All available templates
 */
export const templates: ContractTemplate[] = [
  helloWorldTemplate,
  counterTemplate,
];

/**
 * Get template by ID
 */
export function getTemplate(id: string): ContractTemplate | undefined {
  return templates.find((t) => t.id === id);
}
