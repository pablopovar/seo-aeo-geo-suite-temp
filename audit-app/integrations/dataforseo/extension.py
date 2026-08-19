from flask import redirect, render_template, request, url_for
from . import connector as dfs

def register_dataforseo_extension(app,research_db,get_site,get_sites):
    @app.get("/d/<domain>/extensions/dataforseo")
    def dataforseo_extension(domain):
        site=get_site(domain)
        with research_db() as con:
            st=dfs.status(con,site["domain"])
            usage=dfs.usage_summary(con,site["domain"])
        return render_template("dataforseo.html",sites=get_sites(),site=site,dfs=st,usage=usage,
                               message=request.args.get("message","").strip())

    @app.post("/d/<domain>/extensions/dataforseo/settings")
    def dataforseo_settings(domain):
        site=get_site(domain)
        enabled=1 if request.form.get("enabled")=="1" else 0
        try: calls=max(0,min(20,int(request.form.get("max_calls_per_run","2"))))
        except Exception: calls=2
        try: items=max(1,min(1000,int(request.form.get("max_items_per_run","10"))))
        except Exception: items=10
        with research_db() as con:
            dfs.ensure_schema(con); dfs.get_project(con,site["domain"])
            con.execute("""UPDATE extension_project SET enabled=?,max_items_per_run=?,
                         max_calls_per_run=?,updated_at=?
                         WHERE domain=? AND extension_key='dataforseo'""",
                        (enabled,items,calls,dfs.now(),site["domain"]))
            con.commit()
        return redirect(url_for("dataforseo_extension",domain=site["domain"],message="DataForSEO project settings saved."))

    @app.post("/d/<domain>/extensions/dataforseo/kill-switch")
    def dataforseo_kill_switch(domain):
        site=get_site(domain)
        kill=1 if request.form.get("kill")=="1" else 0
        with research_db() as con:
            dfs.ensure_schema(con)
            con.execute("UPDATE extension_global SET kill_switch=?,updated_at=? WHERE extension_key='dataforseo'",
                        (kill,dfs.now()))
            con.commit()
        msg="DataForSEO kill switch ON. All API calls disabled." if kill else "DataForSEO kill switch OFF."
        return redirect(url_for("dataforseo_extension",domain=site["domain"],message=msg))

    @app.post("/d/<domain>/extensions/dataforseo/pull-minimal")
    def dataforseo_pull_minimal(domain):
        site=get_site(domain)
        with research_db() as con:
            try:
                dfs.minimal_conversation_starter(con,site["domain"])
                msg="Minimal DataForSEO dataset retrieved."
            except Exception as exc:
                msg=f"DataForSEO pull blocked/failed: {exc}"
        return redirect(url_for("dataforseo_extension",domain=site["domain"],message=msg))
