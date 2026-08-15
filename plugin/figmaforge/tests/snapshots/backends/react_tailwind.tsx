import React from 'react';

export function Landing({ className = '' }: { className?: string }) {
  return (
      <div data-figma-id="0:1" name="Landing" className="flex flex-col justify-center items-center gap-[24px] pt-[24px] pr-[24px] pb-[24px] pl-[24px] w-[400px] h-[600px]">
        <span data-figma-id="t:1" name="Title" className="block text-[32px] font-bold font-['Inter'] text-center">Welcome</span>
        <div data-figma-id="btn:1" name="Button" className="flex flex-row w-[120px] h-[48px] bg-[#3366cc] rounded-[8px]">
          <span data-figma-id="t:2" name="Label" className="block text-[16px] font-semibold font-['Inter']">Click me</span>
        </div>
      </div>
  );
}

export default Landing;
