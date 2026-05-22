# CODE PROJECTS

*W.A.D.E. reads this file on every request to understand your active codebases.
Add a section per project. The live indexer will semantically search the actual source files;
this file gives W.A.D.E. the high-level map so it can orient itself fast.*

*To register a project directory for deep indexing, add its path to `indexer.project_dirs` in
`~/.wade/config.yaml`:*

```yaml
indexer:
  project_dirs:
    - C:\Users\you\Projects\my-app
    - C:\Users\you\Projects\client-site
```

---

## Project Template

### [Project Name]

- **Path:** `C:\Users\you\Projects\project-name`
- **Stack:** e.g. Python / FastAPI / React / PostgreSQL
- **Purpose:** One sentence describing what this project does.
- **Entry points:** e.g. `app/main.py`, `src/index.tsx`
- **Key modules:** e.g. `app/services/` handles X, `app/models/` defines Y
- **Status:** Active / On-hold / Archived
- **Notes:** Any context W.A.D.E. should keep in mind (deploy target, known issues, etc.)

---

<!-- Copy the template above for each project you want W.A.D.E. to know about. -->
