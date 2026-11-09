// Who runs this site, from the container's environment (CATEGORY_CONTACT_EMAIL, CATEGORY_OPERATOR_NAME).
import "server-only";

export const contactEmail = () => process.env.CONTACT_EMAIL?.trim() || null;
export const operatorName = () => process.env.OPERATOR_NAME?.trim() || null;
