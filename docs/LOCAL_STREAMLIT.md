# INART PM Local Streamlit

The local runner forces JSON storage and local attachment files instead of MongoDB/GridFS. By default, local data is stored under `.local_data/`, which is ignored by Git.

## Start

Run:

```powershell
.\run_local.bat
```

If dependencies are missing on the first run:

```powershell
.\run_local.bat -InstallDeps
```

Default URL:

```text
http://localhost:8501
```

## Custom Port Or Data Directory

```powershell
.\run_local.bat -Port 8502
.\run_local.bat -DataDir D:\INART_PM_Data
```

## Data Location

Default data file:

```text
.local_data\tracker_data_web_v20.json
```

Default attachment directory:

```text
.local_data\img_assets\
```

To move cloud data locally, download a full backup from the sidebar, start the local app, then restore that backup from the sidebar.
