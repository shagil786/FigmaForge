import 'package:flutter/material.dart';

// FigmaForge generated Flutter widget
// Source: LayoutPlan node 0:1

class LandingScreen extends StatelessWidget {
  const LandingScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SizedBox(
        width: 390,
        child: Container(
          width: 400,
          height: 600,
          padding: EdgeInsets.all(24),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Align(
                alignment: Alignment.center,
                child: Text(
                    'Welcome',
                    style: TextStyle(
                      fontFamily: 'Inter',
                      fontSize: 32,
                      fontWeight: FontWeight.w700,
                      color: Color(0xFF141F3D),
                    ),
                    textAlign: TextAlign.center,
                    ),
              ),
              SizedBox(height: 24),
              Container(
                width: 120,
                height: 48,
                decoration: BoxDecoration(
                  color: Color(0xFF3366CC),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Row(
                  children: [
                    Text(
                      'Click me',
                      style: TextStyle(
                        fontFamily: 'Inter',
                        fontSize: 16,
                        fontWeight: FontWeight.w600,
                        height: 1.5,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
