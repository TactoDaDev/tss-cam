// The camera app: full-screen view, status pill on top, controls on dark glass along the bottom.
// Design language: the user's Backtrack / BBG Terminal (white controls, pills, one hero control: the shutter).
import ARKit
import SwiftUI

let MODE_LABELS = ["hand": "Hand", "ride": "Ride", "hood": "Hood", "passenger": "Passenger", "chase": "Chase",
                   "drone": "Drone", "lockon": "Lock-on"]
let GAME_LABELS = ["beamng": "BeamNG", "assetto": "Assetto Corsa"]

struct ContentView: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var link: Link

    var body: some View {
        ZStack {
            CameraView(session: model.tracker.session).ignoresSafeArea()
            VStack(spacing: 12) {
                topBar
                if !link.lastError.isEmpty { errorPill }
                Spacer()
                sticks
                if let caps = link.caps, !caps.modes.isEmpty { modePills(caps) }
                zoomChips
                tabs
                shutterRow
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 8)
        }
        .sheet(isPresented: $model.showConnect) { ConnectSheet().environmentObject(model).environmentObject(link).environmentObject(AppLog.shared) }
        .sheet(isPresented: $model.showCar) { CarSheet().environmentObject(link) }
    }

    private var topBar: some View {
        HStack {
            HStack(spacing: 8) {
                Circle().fill(link.live ? Color.green : (link.host.isEmpty ? Color.gray : Color.orange)).frame(width: 8, height: 8)
                Text(statusText).font(.system(size: 12, weight: .heavy)).tracking(1).lineLimit(1)
            }
            .padding(.horizontal, 12).padding(.vertical, 7)
            .background(Color.black.opacity(0.55), in: Capsule())
            .overlay(Capsule().stroke(Color.white.opacity(0.12)))
            Spacer()
            Button { model.showConnect = true } label: {
                Image(systemName: "gearshape.fill").font(.system(size: 16, weight: .semibold))
                    .frame(width: 36, height: 36).background(Color.black.opacity(0.55), in: Circle())
            }
        }
        .foregroundStyle(Color.white)
        .padding(.top, 8)
    }

    private var statusText: String {
        guard link.live else { return link.host.isEmpty ? "NOT CONNECTED" : "CONNECTING" }
        var parts = ["LIVE"]
        if let g = link.caps?.game { parts.append((GAME_LABELS[g] ?? g).uppercased()) }
        if let r = link.rttMs { parts.append("\(Int(r)) MS") }
        if model.frozen { parts.append("FROZEN") }
        if model.trackingText != "Tracking" { parts.append(model.trackingText.uppercased()) }
        return parts.joined(separator: " · ")
    }

    private var errorPill: some View {
        Text(link.lastError).font(.system(size: 12, weight: .semibold)).foregroundStyle(Color.white)
            .padding(.horizontal, 12).padding(.vertical, 6)
            .background(Color(red: 0.94, green: 0.29, blue: 0.23).opacity(0.85), in: Capsule())
    }

    private var sticks: some View {
        HStack(alignment: .bottom) {
            if model.tab == .walk {
                Thumbstick(value: $model.move, label: "MOVE")
            }
            Spacer()
            if link.caps != nil {
                Thumbstick(value: Binding(get: { CGPoint(x: model.look, y: -model.lift) },
                                          set: { model.look = $0.x; model.lift = -$0.y }), label: "LIFT · TURN")
            }
        }
    }

    private func modePills(_ caps: Caps) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(caps.modes, id: \.self) { m in
                    Button { link.command("cam.mode", ["mode": m]) } label: {
                        Text(MODE_LABELS[m] ?? m).font(.system(size: 13, weight: .bold))
                            .padding(.horizontal, 14).padding(.vertical, 7)
                            .foregroundStyle(caps.camMode == m ? Color.black : Color.white)
                            .background(caps.camMode == m ? Color.white : Color.black.opacity(0.5), in: Capsule())
                            .overlay(Capsule().stroke(Color.white.opacity(caps.camMode == m ? 0 : 0.18)))
                    }
                }
            }
        }
    }

    private var zoomChips: some View {
        HStack(spacing: 8) {
            ForEach([Float(0.5), 1, 2, 3], id: \.self) { z in
                Button { model.zoom = z } label: {
                    Text(z == 0.5 ? ".5" : "\(Int(z))").font(.system(size: 13, weight: .bold))
                        .frame(width: 38, height: 38)
                        .foregroundStyle(model.zoom == z ? Color.yellow : Color.white)
                        .background(Color.black.opacity(0.5), in: Circle())
                }
            }
        }
    }

    private var tabs: some View {
        HStack(spacing: 28) {
            ForEach(Tab.allCases, id: \.self) { t in
                Button {
                    if t == .car { model.showCar = true } else { model.tab = t }
                } label: {
                    Text(t.rawValue).font(.system(size: 13, weight: .heavy)).tracking(1.4)
                        .foregroundStyle(model.tab == t ? Color.yellow : Color.white.opacity(0.85))
                }
            }
        }
    }

    private var shutterRow: some View {
        HStack {
            roundButton("scope", "Recenter") { model.recenter() }
            Spacer()
            Shutter(filming: link.caps?.engaged ?? false, frozen: $model.frozen) {
                if link.caps != nil { link.command("cam.toggle") } else { model.recenter() }
            }
            Spacer()
            roundButton("car.fill", "Car") { model.showCar = true }
        }
    }

    private func roundButton(_ icon: String, _ label: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(spacing: 4) {
                Image(systemName: icon).font(.system(size: 20, weight: .semibold))
                    .frame(width: 52, height: 52).background(Color.black.opacity(0.55), in: Circle())
                    .overlay(Circle().stroke(Color.white.opacity(0.15)))
                Text(label).font(.system(size: 10, weight: .bold))
            }.foregroundStyle(Color.white)
        }
    }
}

/// The hero control: tap = film in the game (or recenter on a stock receiver), hold = freeze while held.
struct Shutter: View {
    let filming: Bool
    @Binding var frozen: Bool
    let tap: () -> Void

    var body: some View {
        ZStack {
            Circle().stroke(filming ? AnyShapeStyle(holo) : AnyShapeStyle(Color.white), lineWidth: 5).frame(width: 78, height: 78)
            Circle().fill(frozen ? Color.yellow : (filming ? Color(red: 0.94, green: 0.29, blue: 0.23) : .white))
                .frame(width: filming ? 34 : 62, height: filming ? 34 : 62)
                .clipShape(RoundedRectangle(cornerRadius: filming ? 8 : 31))
        }
        .contentShape(Circle())
        .onTapGesture { tap() }
        .onLongPressGesture(minimumDuration: 0.3, pressing: { p in
            if !p { frozen = false }
        }, perform: { frozen = true; UIImpactFeedbackGenerator(style: .light).impactOccurred() })
        .animation(.easeOut(duration: 0.15), value: filming)
    }

    private var holo: LinearGradient {
        LinearGradient(colors: [Color(red: 0.49, green: 0.91, blue: 0.98), Color(red: 0.71, green: 0.55, blue: 1),
                                Color(red: 1, green: 0.6, blue: 0.84), Color(red: 1, green: 0.83, blue: 0.54)],
                       startPoint: .topLeading, endPoint: .bottomTrailing)
    }
}

/// A thumbstick: -1...1 on both axes (y down, as the screen), springs back on release.
struct Thumbstick: View {
    @Binding var value: CGPoint
    var label = ""
    var size: CGFloat = 116
    @State private var knob: CGSize = .zero

    var body: some View {
        VStack(spacing: 6) {
            ZStack {
                Circle().fill(Color.black.opacity(0.45)).overlay(Circle().stroke(Color.white.opacity(0.22), lineWidth: 1.5))
                Circle().fill(Color.white).frame(width: size * 0.36, height: size * 0.36).offset(knob)
                    .shadow(color: .black.opacity(0.4), radius: 4)
            }
            .frame(width: size, height: size)
            .gesture(DragGesture(minimumDistance: 0).onChanged { g in
                let r = size / 2, reach = r * 0.62
                var dx = g.location.x - r, dy = g.location.y - r
                let len = (dx * dx + dy * dy).squareRoot()
                if len > reach { dx *= reach / len; dy *= reach / len }
                knob = CGSize(width: dx, height: dy)
                value = CGPoint(x: dx / reach, y: dy / reach)
            }.onEnded { _ in
                withAnimation(.spring(response: 0.2, dampingFraction: 0.7)) { knob = .zero }
                value = .zero
            })
            if !label.isEmpty { Text(label).font(.system(size: 10, weight: .heavy)).tracking(1).foregroundStyle(Color.white.opacity(0.8)) }
        }
    }
}

/// The phone's own camera behind the controls (ARKit owns the camera, so the view shows its session).
struct CameraView: UIViewRepresentable {
    let session: ARSession
    func makeUIView(context: Context) -> ARSCNView {
        let v = ARSCNView()
        v.session = session
        v.automaticallyUpdatesLighting = false
        return v
    }
    func updateUIView(_ v: ARSCNView, context: Context) {}
}
