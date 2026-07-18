use axum::{
    body::Body,
    http::{StatusCode, Uri, header},
    response::{IntoResponse, Response},
};
use rust_embed::RustEmbed;

#[derive(RustEmbed)]
#[folder = "frontend/dist/"]
struct FrontendAssets;

pub async fn serve(uri: Uri) -> Response {
    let path = uri.path().trim_start_matches('/');
    let requested = if path.is_empty() { "index.html" } else { path };

    if let Some(asset) = FrontendAssets::get(requested) {
        return asset_response(requested, asset.data.into_owned());
    }

    // TanStack Router owns client-side routes, so unknown non-file paths receive
    // the SPA shell. Missing asset-like paths remain proper 404 responses.
    if !requested
        .rsplit('/')
        .next()
        .is_some_and(|part| part.contains('.'))
        && let Some(index) = FrontendAssets::get("index.html")
    {
        return asset_response("index.html", index.data.into_owned());
    }

    (StatusCode::NOT_FOUND, "Not found").into_response()
}

fn asset_response(path: &str, bytes: Vec<u8>) -> Response {
    let mime = mime_guess::from_path(path).first_or_octet_stream();
    let cache_control = if path == "index.html" {
        "no-cache"
    } else {
        "public, max-age=31536000, immutable"
    };

    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, mime.as_ref())
        .header(header::CACHE_CONTROL, cache_control)
        .body(Body::from(bytes))
        .expect("static asset response is valid")
}
