import { describe, it, expect } from "vitest";
import { createProject } from "../src/project/storage/db";

describe("Project Management", () => {
  it("should create a new project", () => {
    const project = createProject("Test Project", "A test project");
    
    expect(project.name).toBe("Test Project");
    expect(project.description).toBe("A test project");
    expect(project.files.length).toBeGreaterThan(0);
    expect(project.files.some(f => f.path === "src/main.py")).toBe(true);
    expect(project.files.some(f => f.path === "manifest.json")).toBe(true);
  });

  it("should generate unique project IDs", () => {
    const project1 = createProject("Project 1");
    const project2 = createProject("Project 2");
    
    expect(project1.id).not.toBe(project2.id);
  });
});
