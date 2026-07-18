mod cli;
mod shutdown;

use std::{future::IntoFuture, sync::Arc};

use anyhow::Context;
use axum::{
    Router,
    extract::Path,
    http::{StatusCode, header},
    response::{IntoResponse, Response},
    routing::get,
};
use clap::Parser;
use cli::Cli;
use music_auto_show::{
    api::GrpcApi,
    app::App,
    assets,
    checkpoint::{CheckpointProvision, ensure_beatnet_checkpoint},
    proto::v1::music_auto_show_service_server::MusicAutoShowServiceServer,
};
use shutdown::ShutdownSignals;
use tokio_util::sync::CancellationToken;
use tonic::service::Routes;
use tower_http::trace::TraceLayer;
use tracing::{info, warn};
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()))
        .init();

    run(cli).await
}

async fn run(cli: Cli) -> anyhow::Result<()> {
    let shutdown_timeout = cli.shutdown_timeout();
    let mut signals = ShutdownSignals::new().context("failed to register shutdown signals")?;
    let listener = tokio::net::TcpListener::bind(cli.listen)
        .await
        .with_context(|| format!("failed to bind {}", cli.listen))?;
    let app_state = Arc::new(App::load(cli.config, cli.simulate).await?);
    prepare_beatnet_checkpoint(&app_state).await;
    app_state.start_runtime().await?;

    let grpc = MusicAutoShowServiceServer::new(GrpcApi::new(Arc::clone(&app_state)));
    let grpc_routes = Routes::new(grpc)
        .into_axum_router()
        .layer(tonic_web::GrpcWebLayer::new());
    let artwork_app = Arc::clone(&app_state);
    let router = Router::new()
        .route(
            "/media/artwork/{revision}",
            get(move |Path(revision): Path<String>| {
                let app = Arc::clone(&artwork_app);
                async move { serve_media_artwork(&app, &revision).await }
            }),
        )
        .nest("/api", grpc_routes)
        .fallback(assets::serve)
        .layer(TraceLayer::new_for_http());

    info!("Music Auto Show is ready at http://{}", cli.listen);

    let server_shutdown = CancellationToken::new();
    let shutdown_requested = server_shutdown.clone();
    let server = axum::serve(listener, router)
        .with_graceful_shutdown(shutdown_requested.cancelled_owned())
        .into_future();
    tokio::pin!(server);

    let shutdown_event = tokio::select! {
        result = &mut server => {
            app_state.stop_runtime().await;
            result.context("HTTP server stopped unexpectedly")?;
            info!("Music Auto Show stopped");
            return Ok(());
        }
        event = signals.recv() => event,
    };
    let shutdown_event = match shutdown_event {
        Ok(event) => event,
        Err(error) => {
            server_shutdown.cancel();
            app_state.stop_runtime().await;
            return Err(error).context("failed while listening for a shutdown event");
        }
    };

    info!(event = %shutdown_event, "graceful shutdown requested");
    server_shutdown.cancel();

    let graceful_shutdown = async {
        app_state.stop_runtime().await;
        server
            .await
            .context("HTTP server failed during graceful shutdown")
    };
    tokio::pin!(graceful_shutdown);

    tokio::select! {
        result = &mut graceful_shutdown => result?,
        event = signals.recv() => {
            match event {
                Ok(event) => {
                    warn!(event = %event, "forcing shutdown after a second shutdown event");
                }
                Err(error) => {
                    warn!(%error, "forcing shutdown because the shutdown listener stopped");
                }
            }
        }
        () = tokio::time::sleep(shutdown_timeout) => {
            warn!(
                timeout_seconds = shutdown_timeout.as_secs(),
                "forcing shutdown after the graceful shutdown timeout"
            );
        }
    }

    info!("Music Auto Show stopped");
    Ok(())
}

async fn serve_media_artwork(app: &App, revision: &str) -> Response {
    let Some(bytes) = app.media_artwork(revision).await else {
        return (StatusCode::NOT_FOUND, "Artwork not found").into_response();
    };
    (
        [
            (header::CONTENT_TYPE, "image/jpeg"),
            (header::CACHE_CONTROL, "public, max-age=31536000, immutable"),
            (header::X_CONTENT_TYPE_OPTIONS, "nosniff"),
        ],
        bytes.as_ref().to_vec(),
    )
        .into_response()
}

async fn prepare_beatnet_checkpoint(app: &App) {
    let config = app.config().await;
    let Some(audio) = config.audio else {
        warn!("cannot prepare BeatNet+ checkpoint because audio configuration is missing");
        return;
    };
    let path = audio.beatnet_model_path;
    match ensure_beatnet_checkpoint(&path).await {
        Ok(CheckpointProvision::Present) => {}
        Ok(CheckpointProvision::Downloaded) => {
            info!(path, "downloaded BeatNet+ checkpoint");
        }
        Err(error) => {
            warn!(
                %error,
                path,
                "BeatNet+ checkpoint download failed; continuing with fallback analysis"
            );
        }
    }
}
