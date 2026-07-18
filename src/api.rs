use std::{pin::Pin, sync::Arc, time::Duration};

use futures_core::Stream;
use tonic::{Request, Response, Status};

use crate::{
    app::App,
    config,
    proto::v1::{
        ClearRecordingRequest, ClearRecordingResponse, ControlShowRequest, ControlShowResponse,
        ExportConfigRequest, ExportConfigResponse, GetConfigRequest, GetConfigResponse,
        GetSnapshotRequest, GetSnapshotResponse, ImportConfigRequest, ImportConfigResponse,
        ListAudioDevicesRequest, ListAudioDevicesResponse, ListFixtureProfilesRequest,
        ListFixtureProfilesResponse, ResetConfigRequest, ResetConfigResponse, SetBlackoutRequest,
        SetBlackoutResponse, StartRecordingRequest, StartRecordingResponse, StopRecordingRequest,
        StopRecordingResponse, UpdateConfigRequest, UpdateConfigResponse, WatchSnapshotsRequest,
        WatchSnapshotsResponse, music_auto_show_service_server::MusicAutoShowService,
    },
};

pub struct GrpcApi {
    app: Arc<App>,
}

impl GrpcApi {
    pub fn new(app: Arc<App>) -> Self {
        Self { app }
    }
}

type SnapshotStream = Pin<Box<dyn Stream<Item = Result<WatchSnapshotsResponse, Status>> + Send>>;

#[tonic::async_trait]
impl MusicAutoShowService for GrpcApi {
    type WatchSnapshotsStream = SnapshotStream;

    async fn get_snapshot(
        &self,
        _request: Request<GetSnapshotRequest>,
    ) -> Result<Response<GetSnapshotResponse>, Status> {
        Ok(Response::new(GetSnapshotResponse {
            snapshot: Some(self.app.snapshot().await),
        }))
    }

    async fn watch_snapshots(
        &self,
        request: Request<WatchSnapshotsRequest>,
    ) -> Result<Response<Self::WatchSnapshotsStream>, Status> {
        let interval =
            Duration::from_millis(request.into_inner().interval_ms.clamp(25, 5_000) as u64);
        let mut receiver = self.app.subscribe();
        let stream = async_stream::try_stream! {
            let initial = receiver.borrow().clone();
            yield WatchSnapshotsResponse { snapshot: Some(initial) };
            loop {
                receiver.changed().await.map_err(|_| Status::unavailable("show state stream closed"))?;
                tokio::time::sleep(interval).await;
                let snapshot = receiver.borrow_and_update().clone();
                yield WatchSnapshotsResponse { snapshot: Some(snapshot) };
            }
        };
        Ok(Response::new(Box::pin(stream)))
    }

    async fn get_config(
        &self,
        _request: Request<GetConfigRequest>,
    ) -> Result<Response<GetConfigResponse>, Status> {
        Ok(Response::new(GetConfigResponse {
            config: Some(self.app.config().await),
        }))
    }

    async fn update_config(
        &self,
        request: Request<UpdateConfigRequest>,
    ) -> Result<Response<UpdateConfigResponse>, Status> {
        let config = request
            .into_inner()
            .config
            .ok_or_else(|| Status::invalid_argument("config is required"))?;
        let config = self
            .app
            .update_config(config)
            .await
            .map_err(internal_status)?;
        Ok(Response::new(UpdateConfigResponse {
            config: Some(config),
        }))
    }

    async fn export_config(
        &self,
        _request: Request<ExportConfigRequest>,
    ) -> Result<Response<ExportConfigResponse>, Status> {
        let (json, filename) = self.app.export_config().await.map_err(internal_status)?;
        Ok(Response::new(ExportConfigResponse { json, filename }))
    }

    async fn import_config(
        &self,
        request: Request<ImportConfigRequest>,
    ) -> Result<Response<ImportConfigResponse>, Status> {
        let json = request.into_inner().json;
        if json.trim().is_empty() {
            return Err(Status::invalid_argument("configuration JSON is required"));
        }
        let config = self
            .app
            .import_config(&json)
            .await
            .map_err(|error| Status::invalid_argument(error.to_string()))?;
        Ok(Response::new(ImportConfigResponse {
            config: Some(config),
        }))
    }

    async fn reset_config(
        &self,
        _request: Request<ResetConfigRequest>,
    ) -> Result<Response<ResetConfigResponse>, Status> {
        let config = self.app.reset_config().await.map_err(internal_status)?;
        Ok(Response::new(ResetConfigResponse {
            config: Some(config),
        }))
    }

    async fn list_audio_devices(
        &self,
        _request: Request<ListAudioDevicesRequest>,
    ) -> Result<Response<ListAudioDevicesResponse>, Status> {
        Ok(Response::new(ListAudioDevicesResponse {
            devices: self.app.audio_devices(),
        }))
    }

    async fn list_fixture_profiles(
        &self,
        _request: Request<ListFixtureProfilesRequest>,
    ) -> Result<Response<ListFixtureProfilesResponse>, Status> {
        let config = self.app.config().await;
        let mut profiles = config::default_profiles();
        for profile in config.profiles {
            if let Some(existing) = profiles
                .iter_mut()
                .find(|existing| existing.name == profile.name)
            {
                *existing = profile;
            } else {
                profiles.push(profile);
            }
        }
        Ok(Response::new(ListFixtureProfilesResponse { profiles }))
    }

    async fn control_show(
        &self,
        request: Request<ControlShowRequest>,
    ) -> Result<Response<ControlShowResponse>, Status> {
        let command = request.into_inner().command();
        let result = self.app.control(command).await.map_err(internal_status)?;
        Ok(Response::new(ControlShowResponse {
            result: Some(result),
        }))
    }

    async fn set_blackout(
        &self,
        request: Request<SetBlackoutRequest>,
    ) -> Result<Response<SetBlackoutResponse>, Status> {
        let result = self.app.set_blackout(request.into_inner().enabled).await;
        Ok(Response::new(SetBlackoutResponse {
            result: Some(result),
        }))
    }

    async fn start_recording(
        &self,
        _request: Request<StartRecordingRequest>,
    ) -> Result<Response<StartRecordingResponse>, Status> {
        let status = self
            .app
            .start_recording()
            .await
            .map_err(failed_precondition)?;
        Ok(Response::new(StartRecordingResponse {
            status: Some(status),
        }))
    }

    async fn stop_recording(
        &self,
        _request: Request<StopRecordingRequest>,
    ) -> Result<Response<StopRecordingResponse>, Status> {
        let recording = self
            .app
            .stop_recording()
            .await
            .map_err(failed_precondition)?;
        Ok(Response::new(StopRecordingResponse {
            recording: Some(recording),
        }))
    }

    async fn clear_recording(
        &self,
        _request: Request<ClearRecordingRequest>,
    ) -> Result<Response<ClearRecordingResponse>, Status> {
        let status = self
            .app
            .clear_recording()
            .await
            .map_err(failed_precondition)?;
        Ok(Response::new(ClearRecordingResponse {
            status: Some(status),
        }))
    }
}

fn internal_status(error: anyhow::Error) -> Status {
    Status::internal(error.to_string())
}

fn failed_precondition(error: anyhow::Error) -> Status {
    Status::failed_precondition(error.to_string())
}
