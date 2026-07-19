use std::{pin::Pin, sync::Arc, time::Duration};

use futures_core::Stream;
use tokio::time::MissedTickBehavior;
use tonic::{Request, Response, Status};

use crate::{
    app::{App, AppError},
    config,
    proto::v1::{
        ClearRecordingRequest, ClearRecordingResponse, ConnectBluetoothReceiverDeviceRequest,
        ConnectBluetoothReceiverDeviceResponse, ControlShowRequest, ControlShowResponse,
        DisconnectBluetoothReceiverDeviceRequest, DisconnectBluetoothReceiverDeviceResponse,
        ExportConfigRequest, ExportConfigResponse, ForgetBluetoothReceiverDeviceRequest,
        ForgetBluetoothReceiverDeviceResponse, GetBluetoothReceiverStatusRequest,
        GetBluetoothReceiverStatusResponse, GetConfigRequest, GetConfigResponse,
        GetSnapshotRequest, GetSnapshotResponse, ImportConfigRequest, ImportConfigResponse,
        ListAudioDevicesRequest, ListAudioDevicesResponse, ListFixtureProfilesRequest,
        ListFixtureProfilesResponse, ResetConfigRequest, ResetConfigResponse, SetBlackoutRequest,
        SetBlackoutResponse, SetBluetoothReceiverPairingRequest,
        SetBluetoothReceiverPairingResponse, StartRecordingRequest, StartRecordingResponse,
        StopRecordingRequest, StopRecordingResponse, UpdateConfigRequest, UpdateConfigResponse,
        WatchSnapshotsRequest, WatchSnapshotsResponse,
        music_auto_show_service_server::MusicAutoShowService,
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
        let stream = async_stream::stream! {
            let initial = receiver.borrow_and_update().snapshot.as_ref().clone();
            yield Ok(WatchSnapshotsResponse { snapshot: Some(initial) });
            let mut ticker = tokio::time::interval_at(tokio::time::Instant::now() + interval, interval);
            ticker.set_missed_tick_behavior(MissedTickBehavior::Skip);
            let mut pending = None;
            loop {
                tokio::select! {
                    () = app.wait_for_shutdown() => break,
                    changed = receiver.changed() => {
                        if changed.is_err() {
                            yield Err(Status::unavailable("show state stream closed"));
                            break;
                        }
                        pending = Some(receiver.borrow_and_update().snapshot.as_ref().clone());
                    }
                    _ = ticker.tick(), if pending.is_some() => {
                        if let Some(snapshot) = pending.take() {
                            yield Ok(WatchSnapshotsResponse { snapshot: Some(snapshot) });
                        }
                    }
                }
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

    async fn get_bluetooth_receiver_status(
        &self,
        _request: Request<GetBluetoothReceiverStatusRequest>,
    ) -> Result<Response<GetBluetoothReceiverStatusResponse>, Status> {
        Ok(Response::new(GetBluetoothReceiverStatusResponse {
            status: Some(self.app.bluetooth_receiver_status().await),
        }))
    }

    async fn set_bluetooth_receiver_pairing(
        &self,
        request: Request<SetBluetoothReceiverPairingRequest>,
    ) -> Result<Response<SetBluetoothReceiverPairingResponse>, Status> {
        let request = request.into_inner();
        let status = self
            .app
            .set_bluetooth_receiver_pairing(request.enabled, request.timeout_seconds)
            .await
            .map_err(app_status)?;
        Ok(Response::new(SetBluetoothReceiverPairingResponse {
            status: Some(status),
        }))
    }

    async fn connect_bluetooth_receiver_device(
        &self,
        request: Request<ConnectBluetoothReceiverDeviceRequest>,
    ) -> Result<Response<ConnectBluetoothReceiverDeviceResponse>, Status> {
        let device_id = required_device_id(request.into_inner().device_id)?;
        let status = self
            .app
            .connect_bluetooth_receiver_device(&device_id)
            .await
            .map_err(app_status)?;
        Ok(Response::new(ConnectBluetoothReceiverDeviceResponse {
            status: Some(status),
        }))
    }

    async fn disconnect_bluetooth_receiver_device(
        &self,
        request: Request<DisconnectBluetoothReceiverDeviceRequest>,
    ) -> Result<Response<DisconnectBluetoothReceiverDeviceResponse>, Status> {
        let device_id = required_device_id(request.into_inner().device_id)?;
        let status = self
            .app
            .disconnect_bluetooth_receiver_device(&device_id)
            .await
            .map_err(app_status)?;
        Ok(Response::new(DisconnectBluetoothReceiverDeviceResponse {
            status: Some(status),
        }))
    }

    async fn forget_bluetooth_receiver_device(
        &self,
        request: Request<ForgetBluetoothReceiverDeviceRequest>,
    ) -> Result<Response<ForgetBluetoothReceiverDeviceResponse>, Status> {
        let device_id = required_device_id(request.into_inner().device_id)?;
        let status = self
            .app
            .forget_bluetooth_receiver_device(&device_id)
            .await
            .map_err(app_status)?;
        Ok(Response::new(ForgetBluetoothReceiverDeviceResponse {
            status: Some(status),
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

fn required_device_id(device_id: String) -> Result<String, Status> {
    let device_id = device_id.trim();
    if device_id.is_empty() {
        return Err(Status::invalid_argument("Bluetooth device ID is required"));
    }
    Ok(device_id.into())
}

fn app_status(error: AppError) -> Status {
    let message = error.to_string();
    match error {
        AppError::Config(error) if error.is_invalid_input() => Status::invalid_argument(message),
        AppError::FailedPrecondition(_) => Status::failed_precondition(message),
        AppError::ResourceExhausted => Status::resource_exhausted(message),
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
