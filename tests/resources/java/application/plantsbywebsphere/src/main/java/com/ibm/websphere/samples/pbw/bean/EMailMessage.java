//
// COPYRIGHT LICENSE: This information contains sample code provided in source code form. You may copy, 
// modify, and distribute these sample programs in any form without payment to IBM for the purposes of 
// developing, using, marketing or distributing application programs conforming to the application 
// programming interface for the operating platform for which the sample code is written. 
// Notwithstanding anything to the contrary, IBM PROVIDES THE SAMPLE SOURCE CODE ON AN "AS IS" BASIS 
// AND IBM DISCLAIMS ALL WARRANTIES, EXPRESS OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, ANY IMPLIED 
// WARRANTIES OR CONDITIONS OF MERCHANTABILITY, SATISFACTORY QUALITY, FITNESS FOR A PARTICULAR PURPOSE, 
// TITLE, AND ANY WARRANTY OR CONDITION OF NON-INFRINGEMENT. IBM SHALL NOT BE LIABLE FOR ANY DIRECT, 
// INDIRECT, INCIDENTAL, SPECIAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE USE OR OPERATION OF THE 
// SAMPLE SOURCE CODE. IBM HAS NO OBLIGATION TO PROVIDE MAINTENANCE, SUPPORT, UPDATES, ENHANCEMENTS 
// OR MODIFICATIONS TO THE SAMPLE SOURCE CODE.  
//
// (C) COPYRIGHT International Business Machines Corp., 2001,2011
// All Rights Reserved * Licensed Materials - Property of IBM
//
package com.ibm.websphere.samples.pbw.bean;

/**
 * This class encapsulates the info needed to send an email message. This object is passed to the
 * Mailer EJB sendMail() method.
 */
public class EMailMessage implements java.io.Serializable {
	/**
	 * 
	 */
	private static final long serialVersionUID = 1L;
	private String subject;
	private String htmlContents;
	private String emailReceiver;

	public EMailMessage(String subject, String htmlContents, String emailReceiver) {
		this.subject = subject;
		this.htmlContents = htmlContents;
		this.emailReceiver = emailReceiver;
	}

	// subject field of email message
	public String getSubject() {
		return subject;
	}

	// Email address of recipient of email message
	public String getEmailReceiver() {
		return emailReceiver;
	}

	// contents of email message
	public String getHtmlContents() {
		return htmlContents;
	}

	public String toString() {
		return " subject=" + subject + " " + emailReceiver + " " + htmlContents;
	}

}
