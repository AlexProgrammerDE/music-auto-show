use std::{pin::Pin, sync::Arc, time::Duration};

use futures_core::Stream;
use tonic::{Request, Response, Status};

use crate::{
    app::{App, AppError},
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
        let app = Arc::clone(&self.app);
        let stream = async_stream::try_stream! {
            let initial = receiver.borrow().as_ref().clone();
            yield WatchSnapshotsResponse { snapshot: Some(initial) };
            loop {
                let changed = tokio::select! {
                    biased;
                    () = app.wait_for_shutdown() => break,
                    changed = receiver.changed() => changed,
                };
                changed.map_err(|_| Status::unavailable("show state stream closed"))?;
                tokio::select! {
                    biased;
                    () = app.wait_for_shutdown() => break,
                    () = tokio::time::sleep(interval) => {}
                }
                let snapshot = receiver.borrow_and_update().as_ref().clone();
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
        let config = self.app.update_config(config).await.map_err(app_status)?;
        Ok(Response::new(UpdateConfigResponse {
            config: Some(config),
        }))
    }

    async fn export_config(
        &self,
        _request: Request<ExportConfigRequest>,
    ) -> Result<Response<ExportConfigResponse>, Status> {
        let (json, filename) = self.app.export_config().await.map_err(app_status)?;
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
        let config = self.app.import_config(&json).await.map_err(app_status)?;
        Ok(Response::new(ImportConfigResponse {
            config: Some(config),
        }))
    }

    async fn reset_config(
        &self,
        _request: Request<ResetConfigRequest>,
    ) -> Result<Response<ResetConfigResponse>, Status> {
        let config = self.app.reset_config().await.map_err(app_status)?;
        Ok(Response::new(ResetConfigResponse {
            config: Some(config),
        }))
    }

    async fn list_audio_devices(
        &self,
        _request: Request<ListAudioDevicesRequest>,
    ) -> Result<Response<ListAudioDevicesResponse>, Status> {
        Ok(Response::new(ListAudioDevicesResponse {
            devices: self.app.audio_devices().await.map_err(app_status)?,
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
        let result = self.app.control(command).await.map_err(app_status)?;
        Ok(Response::new(ControlShowResponse {
            result: Some(result),
        }))
    }

    async fn set_blackout(
        &self,
        request: Request<SetBlackoutRequest>,
    ) -> Result<Response<SetBlackoutResponse>, Status> {
        let result = self
            .app
            .set_blackout(request.into_inner().enabled)
            .await
            .map_err(app_status)?;
        Ok(Response::new(SetBlackoutResponse {
            result: Some(result),
        }))
    }

    async fn start_recording(
        &self,
        _request: Request<StartRecordingRequest>,
    ) -> Result<Response<StartRecordingResponse>, Status> {
        let status = self.app.start_recording().await.map_err(app_status)?;
        Ok(Response::new(StartRecordingResponse {
            status: Some(status),
        }))
    }

    async fn stop_recording(
        &self,
        _request: Request<StopRecordingRequest>,
    ) -> Result<Response<StopRecordingResponse>, Status> {
        let recording = self.app.stop_recording().await.map_err(app_status)?;
        Ok(Response::new(StopRecordingResponse {
            recording: Some(recording),
        }))
    }

    async fn clear_recording(
        &self,
        _request: Request<ClearRecordingRequest>,
    ) -> Result<Response<ClearRecordingResponse>, Status> {
        let status = self.app.clear_recording().await.map_err(app_status)?;
        Ok(Response::new(ClearRecordingResponse {
            status: Some(status),
        }))
    }
}

fn app_status(error: AppError) -> Status {
    let message = error.to_string();
    match error {
        AppError::Config(error) if error.is_invalid_input() => Status::invalid_argument(message),
        AppError::FailedPrecondition(_) => Status::failed_precondition(message),
        AppError::Unavailable | AppError::Runtime(_) => Status::unavailable(message),
        AppError::Config(_) => Status::internal(message),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio_stream::StreamExt;

    #[tokio::test]
    async fn snapshot_stream_closes_when_application_stops() {
        let directory = tempfile::tempdir().expect("temporary directory should be created");
        let app = Arc::new(
            App::load(directory.path().join("config.json"), true)
                .await
                .expect("simulated application should load"),
        );
        let api = GrpcApi::new(Arc::clone(&app));
        let response = api
            .watch_snapshots(Request::new(WatchSnapshotsRequest { interval_ms: 25 }))
            .await
            .expect("snapshot stream should start");
        let mut stream = response.into_inner();

        let initial = stream
            .next()
            .await
            .expect("snapshot stream should yield an initial item")
            .expect("initial snapshot should be valid");
        assert!(initial.snapshot.is_some());

        app.stop_runtime().await;

        let next = tokio::time::timeout(Duration::from_secs(1), stream.next())
            .await
            .expect("snapshot stream should stop promptly");
        assert!(next.is_none());
    }

    #[tokio::test]
    async fn invalid_structured_config_returns_invalid_argument() {
        let directory = tempfile::tempdir().expect("temporary directory should be created");
        let app = Arc::new(
            App::load(directory.path().join("config.json"), true)
                .await
                .expect("simulated application should load"),
        );
        app.start_runtime()
            .await
            .expect("show runtime should start");
        let api = GrpcApi::new(Arc::clone(&app));
        let mut config = app.config().await;
        config.audio.as_mut().expect("audio configuration").mode = i32::MAX;

        let error = api
            .update_config(Request::new(UpdateConfigRequest {
                config: Some(config),
            }))
            .await
            .expect_err("invalid configuration should be rejected");

        assert_eq!(error.code(), tonic::Code::InvalidArgument);
        app.stop_runtime().await;
    }
}
