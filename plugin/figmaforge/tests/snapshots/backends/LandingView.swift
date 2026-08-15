import SwiftUI

// FigmaForge generated SwiftUI view
// Source: LayoutPlan node 0:1

struct LandingView: View {
    var body: some View {
    VStack(alignment: .trailing, spacing: 24) {
      Text("Welcome")
        .foregroundColor(Color(red: 0.08, green: 0.12, blue: 0.24))
        .font(.system(size: 32, weight: .bold))
        .multilineTextAlignment(.center)
      HStack {
        Text("Click me")
          .font(.system(size: 16, weight: .semibold))
          .lineSpacing(24)
          .kerning(0.5)
      }
        .background(Color(red: 0.20, green: 0.40, blue: 0.80))
        .frame(width: 120, height: 48)
        .cornerRadius(8)
        .opacity(0.9)
    }
      .frame(width: 400, height: 600)
      .padding(24)
    }
}

#Preview {
    LandingView()
}
