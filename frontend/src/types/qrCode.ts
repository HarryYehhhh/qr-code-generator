export interface CreateQRCodeResponse {
  qr_token: string;
}

export interface GetQRCodeResponse {
  url: string;
  click_count: number;
  status: string;
  created_at: string;
}

export interface QRCodeListItem {
  qr_token: string;
  url: string;
  click_count: number;
  status: string;
  created_at: string;
}
