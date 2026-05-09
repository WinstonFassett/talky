const std = @import("std");
const runner = @import("runner");
const zero_native = @import("zero-native");

pub const panic = std.debug.FullPanic(zero_native.debug.capturePanic);

const daemon_url = "http://localhost:9090/?autoconnect=true";

const App = struct {
    fn app(self: *@This()) zero_native.App {
        return .{
            .context = self,
            .name = "talky-shell",
            .source = zero_native.WebViewSource.url(daemon_url),
        };
    }
};

const allowed_origins = [_][]const u8{
    "zero://app",
    "zero://inline",
    "http://localhost:9090",
    "http://127.0.0.1:9090",
};

pub fn main(init: std.process.Init) !void {
    var app = App{};
    try runner.runWithOptions(app.app(), .{
        .app_name = "Talky",
        .window_title = "Talky",
        .bundle_id = "dev.zero_native.talky-shell",
        .icon_path = "assets/icon.icns",
        .security = .{
            .navigation = .{ .allowed_origins = &allowed_origins },
        },
    }, init);
}

test "app name is configured" {
    try std.testing.expectEqualStrings("2026-05-09-talky-shell","2026-05-09-talky-shell");
}
