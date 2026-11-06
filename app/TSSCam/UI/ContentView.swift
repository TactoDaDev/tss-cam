// The camera app. TSS Cam's own look (same as the receiver window): a viewfinder over the camera feed (corner
// brackets, focus box, thin overlays), race livery (slanted red tally, heavy italic
// race-number readouts) and glass (translucent capsules, a white shutter with a ring).
import ARKit
import SwiftUI

let MODE_LABELS = ["hand": "Hand", "ride": "Ride", "hood": "Hood", "passenger": "Passenger", "chase": "Chase",
                   "drone": "Drone", "lockon": "Lock-on"]
let GAME_LABELS = ["beamng": "BeamNG", "assetto": "Assetto Corsa"]

enum Livery {
    static let navy = Color(red: 0.05, green: 0.16, blue: 0.36)
    static let sky = Color(red: 0.18, green: 0.55, blue: 0.86)
    static let red = Color(red: 0.91, green: 0.13, blue: 0.16)
    static let amber = Color(red: 1, green: 0.75, blue: 0.1)
    static let glass = Color.black.opacity(0.5)
}

extension Font {
    static func race(_ size: CGFloat) -> Font { .system(size: size, weight: .heavy).italic() }
    static func readout(_ size: CGFloat) -> Font { .system(size: size, weight: .black).italic() }
    static func mono(_ size: CGFloat) -> Font { .system(size: size, weight: .medium, design: .monospaced) }
}

/// A slanted box: livery tags, the tally, tab underlines.
struct Slant: Shape {
    var lean: CGFloat = 6
    func path(in r: CGRect) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: r.minX + lean, y: r.minY))
        p.addLine(to: CGPoint(x: r.maxX, y: r.minY))
        p.addLine(to: CGPoint(x: r.maxX - lean, y: r.maxY))
        p.addLine(to: CGPoint(x: r.minX, y: r.maxY))
        p.closeSubpath()
        return p
    }
}

struct ContentView: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var link: Link

    var body: some View {
        ZStack {
            CameraView(session: model.tracker.session).ignoresSafeArea()
            shades.ignoresSafeArea().allowsHitTesting(false)
            ViewfinderMarks().padding(.horizontal, 22).padding(.top, 70).padding(.bottom, 330).allowsHitTesting(false)
            VStack(spacing: 12) {
                topBar
                if !link.lastError.isEmpty { errorTag }
                Spacer()
                sticks
                readouts
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

    private var shades: some View {
        VStack(spacing: 0) {
            LinearGradient(colors: [.black.opacity(0.65), .clear], startPoint: .top, endPoint: .bottom).frame(height: 130)
            Spacer()
            LinearGradient(colors: [.clear, .black.opacity(0.85)], startPoint: .top, endPoint: .bottom).frame(height: 380)
        }
    }

    private var topBar: some View {
        HStack(spacing: 10) {
            HStack(spacing: 2) {
                Text("TSS").foregroundStyle(Color.white)
                Text("CAM").foregroundStyle(Livery.red)
            }.font(.race(19))
            Spacer()
            Text(tallyText).font(.race(12)).tracking(1.2)
                .padding(.horizontal, 12).padding(.vertical, 4)
                .foregroundStyle(link.live ? Color.white : Color.white.opacity(0.8))
                .background(link.live ? Livery.red : Color.white.opacity(0.16), in: Slant())
            Button { model.showConnect = true } label: {
                Image(systemName: "gearshape.fill").font(.system(size: 15, weight: .semibold))
                    .frame(width: 36, height: 36).background(Livery.glass, in: Circle())
                    .overlay(Circle().stroke(Color.white.opacity(0.15)))
            }
        }
        .foregroundStyle(Color.white)
        .padding(.top, 8)
    }

    private var tallyText: String {
        guard link.live else { return link.host.isEmpty ? "NO LINK" : "CONNECTING" }
        if model.frozen { return "HOLD" }
        if let g = link.caps?.game { return "LIVE · " + (GAME_LABELS[g] ?? g).uppercased() }
        return "LIVE"
    }

    private var errorTag: some View {
        Text(link.lastError.uppercased()).font(.race(11)).foregroundStyle(Color.black)
            .padding(.horizontal, 12).padding(.vertical, 5)
            .background(Livery.amber, in: Slant())
    }

    /// race-number readouts, like the receiver's bottom strip
    private var readouts: some View {
        HStack(alignment: .bottom, spacing: 18) {
            readout("LATENCY", link.rttMs.map { "\(Int($0)) MS" } ?? "-- MS")
            readout("LENS", "\(Int(24 * model.zoom))MM", accent: true)
            readout("TRACK", model.trackingText.uppercased())
            Spacer()
        }
    }

    private func readout(_ k: String, _ v: String, accent: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(k).font(.race(9)).tracking(1.4).foregroundStyle(Livery.sky)
            Text(v).font(.readout(18)).foregroundStyle(accent ? Livery.red : Color.white).lineLimit(1)
        }
    }

    private var sticks: some View {
        HStack(alignment: .bottom) {
            if model.tab == .walk {
                Thumbstick(value: $model.move, label: "MOVE").accessibilityIdentifier("stick-move")
            }
            Spacer()
            if link.caps != nil {
                Thumbstick(value: Binding(get: { CGPoint(x: model.look, y: -model.lift) },
                                          set: { model.look = $0.x; model.lift = -$0.y }), label: "LIFT · TURN")
                    .accessibilityIdentifier("stick-lift")
            }
        }
    }

    private func modePills(_ caps: Caps) -> some View {
        ScrollViewReader { reader in
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(caps.modes, id: \.self) { m in
                        let on = caps.camMode == m
                        Button { link.command("cam.mode", ["mode": m]) } label: {
                            Text((MODE_LABELS[m] ?? m).uppercased()).font(.race(12)).tracking(0.6)
                                .padding(.horizontal, 14).padding(.vertical, 8)
                                .foregroundStyle(Color.white)
                                .background(on ? Livery.red.opacity(0.35) : Livery.glass, in: Capsule())
                                .overlay(Capsule().stroke(on ? Livery.red : Color.white.opacity(0.16), lineWidth: on ? 1.5 : 1))
                        }
                        .id(m)
                        .accessibilityIdentifier("mode-\(m)")
                    }
                }
            }
            // the selected mode is always in view (it can be off the edge of the row)
            .onAppear { reader.scrollTo(caps.camMode, anchor: .center) }
            .onChange(of: caps.camMode) { m in withAnimation(.easeOut(duration: 0.2)) { reader.scrollTo(m, anchor: .center) } }
        }
    }

    private var zoomChips: some View {
        HStack(spacing: 8) {
            ForEach([Float(0.5), 1, 2, 3], id: \.self) { z in
                Button { model.zoom = z } label: {
                    Text(z == 0.5 ? ".5" : "\(Int(z))").font(.race(13))
                        .frame(width: 38, height: 38)
                        .foregroundStyle(model.zoom == z ? Livery.sky : Color.white)
                        .background(Livery.glass, in: Circle())
                        .overlay(Circle().stroke(model.zoom == z ? Livery.sky : Color.white.opacity(0.15)))
                }
            }
        }
    }

    private var tabs: some View {
        HStack(spacing: 26) {
            ForEach(Tab.allCases, id: \.self) { t in
                Button {
                    if t == .car { model.showCar = true } else { model.tab = t }
                } label: {
                    VStack(spacing: 4) {
                        Text(t.rawValue).font(.race(13)).tracking(1.2)
                            .foregroundStyle(model.tab == t ? Color.white : Color.white.opacity(0.6))
                        Slant(lean: 3).fill(model.tab == t ? Livery.red : Color.clear).frame(width: 26, height: 3)
                    }
                }
                .accessibilityIdentifier("tab-\(t.rawValue)")
            }
        }
    }

    private var shutterRow: some View {
        ZStack {
            HStack {
                roundButton("scope", "RECENTER") { model.recenter() }
                Spacer()
                Shutter(filming: link.caps?.engaged ?? false, frozen: $model.frozen) {
                    if link.caps != nil { link.command("cam.toggle") } else { model.recenter() }
                }
                Spacer()
                roundButton("car.fill", "CAR") { model.showCar = true }
            }
        }
    }

    private func roundButton(_ icon: String, _ label: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(spacing: 4) {
                Image(systemName: icon).font(.system(size: 20, weight: .semibold))
                    .frame(width: 52, height: 52).background(Livery.glass, in: Circle())
                    .overlay(Circle().stroke(Color.white.opacity(0.16)))
                Text(label).font(.race(10)).tracking(0.8)
            }.foregroundStyle(Color.white)
        }
        .accessibilityIdentifier(label.lowercased())
    }
}

/// Corner brackets and a focus box over the camera feed.
struct ViewfinderMarks: View {
    var body: some View {
        GeometryReader { g in
            let w = g.size.width, h = g.size.height, l: CGFloat = 26
            Path { p in
                p.move(to: CGPoint(x: 0, y: l)); p.addLine(to: .zero); p.addLine(to: CGPoint(x: l, y: 0))
                p.move(to: CGPoint(x: w - l, y: 0)); p.addLine(to: CGPoint(x: w, y: 0)); p.addLine(to: CGPoint(x: w, y: l))
                p.move(to: CGPoint(x: 0, y: h - l)); p.addLine(to: CGPoint(x: 0, y: h)); p.addLine(to: CGPoint(x: l, y: h))
                p.move(to: CGPoint(x: w - l, y: h)); p.addLine(to: CGPoint(x: w, y: h)); p.addLine(to: CGPoint(x: w, y: h - l))
            }
            .stroke(Color.white.opacity(0.9), lineWidth: 2)
            .shadow(color: .black.opacity(0.6), radius: 2) // still visible over bright scenes
            RoundedRectangle(cornerRadius: 4).stroke(Livery.sky, lineWidth: 1.5)
                .frame(width: 70, height: 70).position(x: w / 2, y: h / 2)
                .shadow(color: Livery.sky.opacity(0.5), radius: 6)
        }
    }
}

/// The hero control: tap = film in the game (or recenter on a stock receiver), hold = freeze while held.
/// One gesture decides by time (released before 0.35 s = tap; held longer = freeze until released): a separate
/// tap and long-press gesture raced, and a tap held a little long (or a busy device) became a freeze instead
/// (the CI end-to-end run lost a "film" tap that way).
struct Shutter: View {
    let filming: Bool
    @Binding var frozen: Bool
    let tap: () -> Void
    @State private var pressed = false
    @State private var holdWork: DispatchWorkItem?

    private var press: some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { _ in
                guard !pressed else { return }
                pressed = true
                let w = DispatchWorkItem {
                    frozen = true
                    UIImpactFeedbackGenerator(style: .light).impactOccurred()
                }
                holdWork = w
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.35, execute: w)
            }
            .onEnded { _ in
                holdWork?.cancel()
                holdWork = nil
                pressed = false
                if frozen { frozen = false } else { tap() }
            }
    }

    var body: some View {
        ZStack {
            Circle().stroke(filming ? AnyShapeStyle(ring) : AnyShapeStyle(Color.white), lineWidth: 5).frame(width: 78, height: 78)
            RoundedRectangle(cornerRadius: filming ? 8 : 31)
                .fill(frozen ? Livery.amber : (filming ? Livery.red : Color.white))
                .frame(width: filming ? 34 : 62, height: filming ? 34 : 62)
        }
        .contentShape(Circle())
        .accessibilityElement(children: .ignore)
        .accessibilityIdentifier("shutter")
        .accessibilityAddTraits(.isButton)
        .accessibilityAction { tap() } // VoiceOver / UI tests activate it as a button
        .gesture(press)
        .scaleEffect(pressed ? 0.94 : 1)
        .animation(.easeOut(duration: 0.15), value: filming)
        .animation(.easeOut(duration: 0.1), value: pressed)
    }

    private var ring: AngularGradient {
        AngularGradient(gradient: Gradient(colors: [Livery.navy, Livery.sky, Livery.red, Livery.navy]), center: .center)
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
                Circle().fill(Livery.glass).overlay(Circle().stroke(Color.white.opacity(0.22), lineWidth: 1.5))
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
            if !label.isEmpty { Text(label).font(.race(10)).tracking(1).foregroundStyle(Livery.sky) }
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
